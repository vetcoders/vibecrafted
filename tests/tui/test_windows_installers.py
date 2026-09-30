"""Coherence checks for the Windows-only EXE/MSI installer cut.

Does not build a pack, does not fetch WiX, and does not write under the
operator LOCALAPPDATA Vibecrafted home.
"""

from __future__ import annotations

import os
import re
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
STABLE_PRODUCT_CODE = "2B1BF36C-C680-48EE-BDCA-648C09D41BB3"
_GUID_RE = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)


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
    assert f'ProductCode = "{STABLE_PRODUCT_CODE}"' in identity
    assert 'ProductName = "Vibecrafted. Framework"' in identity
    assert 'ProductName = "Vibecrafted"' not in identity
    assert 'ProductVersion = "0.0.0.0"' in identity
    assert "<?include Identity.wxi ?>" in product
    assert "<?include Identity.wxi ?>" in bundle
    assert 'UpgradeCode="$(var.UpgradeCode)"' in product
    assert 'UpgradeCode="$(var.UpgradeCode)"' in bundle
    # Same-version rebuilds must keep ProductCode fixed; Id="*" mints a new
    # GUID every candle and MajorUpgrade then dies with WIX_DOWNGRADE_DETECTED.
    assert 'Id="$(var.ProductCode)"' in product
    assert 'Id="*"' not in product
    assert _GUID_RE.match(STABLE_PRODUCT_CODE), STABLE_PRODUCT_CODE
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
    assert STABLE_PRODUCT_CODE in build
    assert "windows-x64" in build
    assert "Write-SiblingSha256" in build
    assert "canonicalStem" in build or "Vibecrafted_${repoVersion}" in build
    assert "Invoke-OptionalAuthenticodeSign" in build
    assert "VIBECRAFTED_WINDOWS_AUTHENTICODE_THUMBPRINT" in build
    assert version.split(".")[0].isdigit()


def test_windows_installer_emits_canonical_windows_x64_names() -> None:
    """MSI/EXE must publish Vibecrafted_<ver>-<date>-<sha>-windows-x64 + .sha256."""
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert "Vibecrafted_${repoVersion}-${releaseDate}-${shortSha}-windows-x64" in build
    assert "Write-SiblingSha256" in build
    assert "MSI (canonical)" in build
    assert "EXE (canonical)" in build
    assert "Authenticode: unsigned unless" in build
    # Short WiX bind names remain for Burn SourceFile=Vibecrafted.msi.
    assert 'Join-Path $OutDir "Vibecrafted.msi"' in build
    assert 'Join-Path $OutDir "Vibecrafted.exe"' in build
    assert STABLE_PRODUCT_CODE in build
    pack_builder = (REPO_ROOT / "scripts" / "build-windows-x64-runtime-pack.ps1").read_text(
        encoding="utf-8"
    )
    assert (
        "Vibecrafted_RuntimePack_${version}-${releaseDate}-${shortSha}-win32-x64.tar.gz"
        in pack_builder
    )
    assert "yyyyMMdd" in pack_builder
    assert "fake donor" not in pack_builder.lower()
    # Half-set VIBECRAFTED_SOURCE_REVISION alone fails distribution_manifest
    # ("environment source provenance must provide an atomic pair").
    assert "VIBECRAFTED_SOURCE_OWNER_REPO" in pack_builder
    assert '$env:VIBECRAFTED_SOURCE_REVISION = $SourceRevision' in pack_builder
    assert 'vetcoders/vibecrafted' in pack_builder


def test_windows_ci_builds_msi_exe_from_pack_artifact() -> None:
    """Install (Windows Matrix) must adapt the uploaded pack into MSI/EXE once.

    Does not rebuild the Runtime Pack. download-artifact stays on the known-good
    v4 SHA. Output stays under packaging/windows/out (no real LOCALAPPDATA install).
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "install-windows.yml").read_text(
        encoding="utf-8"
    )
    assert "windows-installers:" in workflow
    assert "build Windows x64 MSI/EXE (unsigned)" in workflow
    assert "needs: windows-runtime-pack" in workflow
    assert "build-windows-installers.ps1" in workflow
    assert "Download Runtime Pack into build/ for build-windows-installers.ps1" in workflow
    assert (
        "Build MSI/EXE from downloaded pack (no second pack build; out under packaging/windows/out)"
        in workflow
    )
    # Known-good download-artifact v4 pin (cold-install, MSI build, MSI install, EXE install).
    assert (
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4"
        in workflow
    )
    assert workflow.count(
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4"
    ) >= 4
    assert "d3f86a106a0bac45b974a628896ce1e5585c70a7" not in workflow
    assert "path: build" in workflow
    assert "build-windows-x64-runtime-pack.ps1" in workflow
    # Installer build job must consume the pack artifact, not invoke the pack builder.
    # Stop before the later MSI install job, which is allowed to touch LOCALAPPDATA.
    installer_job = workflow.split("windows-installers:", 1)[1]
    if "\n  windows-msi-install:" in installer_job:
        installer_job = installer_job.split("\n  windows-msi-install:", 1)[0]
    assert "build-windows-installers.ps1" in installer_job
    assert "build-windows-x64-runtime-pack.ps1" not in installer_job
    assert "packaging/windows/out/Vibecrafted_*-windows-x64.msi" in installer_job
    assert "packaging/windows/out/Vibecrafted_*-windows-x64.exe" in installer_job
    assert "packaging/windows/out/Vibecrafted_*-windows-x64.msi.sha256" in installer_job
    assert "packaging/windows/out/Vibecrafted_*-windows-x64.exe.sha256" in installer_job
    assert "windows-x64-installers" in installer_job
    assert "VIBECRAFTED_WINDOWS_AUTHENTICODE_THUMBPRINT" not in installer_job
    assert "signtool" not in installer_job.lower()
    assert "LOCALAPPDATA" not in installer_job or "does not install into LOCALAPPDATA" in installer_job
    assert "install.ps1" not in installer_job


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


def test_install_ps1_bare_uninstall_skips_dist_autodiscovery() -> None:
    """Bare ``-Uninstall`` must not bind leftover ``dist/*.tar.gz``.

    CI cold-install leaves the rehearsal-signed pack in ``dist/``. Auto-binding
    it on uninstall would re-verify with ``vibecrafted-signing-v1.pub`` and fail
    instead of removing the installed LOCALAPPDATA generation.
    """
    entry = (REPO_ROOT / "install.ps1").read_text(encoding="utf-8")
    assert "if (-not $hasPack -and -not $Uninstall)" in entry
    assert "Bare -Uninstall must use the installed generation" in entry
    assert 'Filter "Vibecrafted_RuntimePack_*-win32-x64.tar.gz"' in entry


def test_ps1_generation_uninstall_tolerates_python_stderr() -> None:
    """Generation uninstall must not abort on Python stderr under Stop.

    CI runs ``install.ps1 -Uninstall`` via Windows PowerShell 5.1 after cold
    install. Pack Python may print lease recovery on stderr; without Continue
    + ``2>&1``, that becomes terminating NativeCommandError and
    ``uninstall failed`` even though the process exited 0.
    """
    installer = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "if ($Uninstall -and -not $Pack)" in installer
    gen_uninstall = installer.split("if ($Uninstall -and -not $Pack)", 1)[1]
    gen_uninstall = gen_uninstall.split("if (-not $Pack)", 1)[0]
    assert '$ErrorActionPreference = "Continue"' in gen_uninstall
    assert "& $packPython @arguments 2>&1" in gen_uninstall
    assert "exit $uninstallCode" in gen_uninstall


def test_ps1_generation_uninstall_stages_runner_outside_live_tree() -> None:
    """Bare uninstall must not run Python from the generation it deletes.

    ``tools/vibecrafted-current`` resolves into ``releases/<ver>``. Launching
    that tree's ``bin/python.exe`` maps ``bin`` DLLs (observed: libcrypto-3.dll)
    into the uninstall process; Windows then returns WinError 5 on rmtree.
    Stage ``bin``/``scripts``/``vibecrafted-core`` under %TEMP% first.
    """
    installer = INSTALL_SCRIPT.read_text(encoding="utf-8")
    gen_uninstall = installer.split("if ($Uninstall -and -not $Pack)", 1)[1]
    gen_uninstall = gen_uninstall.split("if (-not $Pack)", 1)[0]
    assert "vc-rt-uninstall-" in gen_uninstall
    assert 'foreach ($name in @("bin", "scripts", "vibecrafted-core"))' in gen_uninstall
    assert "Copy-Item -LiteralPath $src -Destination (Join-Path $stageGen $name)" in gen_uninstall
    assert 'Join-Path $stageGen "bin\\python.exe"' in gen_uninstall
    assert "Remove-Item -LiteralPath $stageRoot -Recurse -Force" in gen_uninstall
    # Live generation python is only used as the copy source, never invoked.
    assert "$livePython = Join-Path $generation" in gen_uninstall
    assert "& $livePython" not in gen_uninstall
    assert "& $packPython @arguments 2>&1" in gen_uninstall


def test_ps1_uninstall_clears_windows_product_root() -> None:
    """Customer uninstall must erase %LOCALAPPDATA%\\Vibecrafted.

    Python runtime-uninstall retains ``.installer-backups`` and may leave
    doctor-created crafted_home state plus the install lease. CI cold-install
    requires the disposable product root to be gone after install.ps1 -Uninstall.
    """
    installer = INSTALL_SCRIPT.read_text(encoding="utf-8")
    assert "function Clear-WindowsProductRootAfterUninstall" in installer
    assert "Clear-WindowsProductRootAfterUninstall -ExitCode $uninstallCode" in installer
    assert "Clear-WindowsProductRootAfterUninstall -ExitCode $installCode" in installer
    assert 'Join-Path $local "Vibecrafted"' in installer
    assert "uninstall left residue under $vcProductRoot" in installer


def test_windows_installer_verifies_with_the_key_that_signed_the_pack() -> None:
    """CI rehearsal signatures must not be checked with the product key.

    The deferred MSI custom action cannot see VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY
    from the workflow process. pack-verify.pub is the key staged into the MSI.
    """
    product = PRODUCT.read_text(encoding="utf-8")
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    assert 'Id="PackVerifyPub"' in product
    assert "pack-verify.pub" in product
    assert "-PublicKey" in product
    assert r"trust\pack-verify.pub" in product
    ci_at = build.index("ci-signing.pub (rehearsal artifact)")
    rehearsal_at = build.index("rehearsal.pub beside pack")
    product_at = build.index("vibecrafted-signing-v1.pub (product key)")
    assert ci_at < rehearsal_at < product_at
    assert "-VerifyOnly" in build
    assert "pack-verify.pub" in build
    assert build.index("disagrees with repo VERSION") < build.index("-VerifyOnly")
    assert build.index("-VerifyOnly") < build.index("& $candle")
    assert "must pass -PublicKey pack-verify.pub" in build


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
    # ASCII brand + Framework for WiX RichEdit; keep former name. No math-sans.
    licensed_work = "Vibecrafted. Framework (formerly Vetcoders Skills)."
    math_sans_brand = "𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍"
    assert f"Licensor:             {full_licensor}" in license_text
    assert "Licensor:             Vetcoders" not in license_text
    assert "Licensor:             LibraxisAI\n" not in license_text
    assert "Licensor:             LibraxisAI\r" not in license_text
    assert f"Licensed Work:        {licensed_work}" in license_text
    assert math_sans_brand not in license_text
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
    # RTF must be plain ASCII for MSI/Burn RichEdit (no surrogate \\u brand).
    assert f"Licensed Work:        {licensed_work}" in rtf
    assert math_sans_brand not in rtf
    assert "\\u-10187?" not in rtf
    assert "\\u55349?" not in rtf
    assert "Framework (formerly Vetcoders Skills)." in rtf
    assert "Licensed Work:        Vibecrafted (formerly Vetcoders Skills)." not in rtf
    assert "Business Source License" in rtf
    assert "Individual developers and small teams" in rtf
    assert "fewer than 5" in rtf
    assert "PLACEHOLDER" not in rtf.upper()
    assert "EULA" not in rtf
    assert all(ord(ch) < 128 for ch in rtf)

    assert f'Manufacturer = "{full_licensor}"' in identity
    assert 'Manufacturer = "Vetcoders"' not in identity
    assert 'Manufacturer = "LibraxisAI"' not in identity
    assert f'Copyright="(c) 2024-2026 {full_licensor}"' in bundle
    assert 'Copyright="(c) 2024-2026 Vetcoders"' not in bundle
    assert 'Copyright="(c) 2024-2026 LibraxisAI"' not in bundle
    assert "WixUI_Minimal" in product
    assert "WixUILicenseRtf" in product
    assert "License.rtf" in product
    assert 'WixVariable Id="WixUIBannerBmp" Value="banner.bmp"' in product
    assert 'WixVariable Id="WixUIDialogBmp" Value="dialog.bmp"' in product
    assert "WixUI_Bmp_Banner" not in product
    assert (PACKAGING / "assets" / "banner.bmp").is_file()
    assert (PACKAGING / "assets" / "dialog.bmp").is_file()
    assert (PACKAGING / "assets" / "burn-logo.bmp").is_file()
    assert "ARPHELPLINK" in product
    assert "SECURITY.md" in product
    assert "hello@vetcoders.io" in product
    assert "ARPCONTACT" in product
    assert "hello@vetcoders.io" in SECURITY_MD.read_text(encoding="utf-8")

    assert "RtfLicense" in bundle
    assert 'LicenseFile="License.rtf"' in bundle
    assert 'LogoFile="burn-logo.bmp"' in bundle
    assert "HyperlinkLicense" not in bundle

    assert "License.rtf" in build
    assert "WixUIExtension" in build
    assert "WixUIBannerBmp" in build
    assert "burn-logo.bmp" in build
    assert "Business Source License" in build
    assert "License.rtf" in readme
    assert "BUSL" in readme or "LICENSE" in readme
    assert "Vibecrafted. Framework" in identity
    assert "banner.bmp" in readme or "near-black" in readme or "0a0a0b" in readme


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
    assert "\\uc1" not in rendered
    assert "\\rtf1\\ansi\\ansicpg1252" in rendered
    assert "\r" not in rendered
    assert "\x00" not in rendered
    assert on_disk == rendered
    assert on_disk != source
    for line in source.splitlines():
        if line.isascii() and line and all(ch not in line for ch in "\\{}"):
            assert line in rendered
    # LICENSE is ASCII now; the escape path still has to be signed UTF-16.
    assert re.search(r"\\u-?\d", rendered) is None
    escaped = render_license_rtf("Licensed Work:        \U0001d685.\n")
    assert "\\u-10187?\\u-8571?" in escaped
    assert "\\u55349?" not in escaped
    drifted = source.replace("BUSL-1.1", "BUSL-DRIFT", 1)
    assert drifted != source
    assert render_license_rtf(drifted) != rendered
    assert main(["--check"]) == 0

    assert 'WixVariable Id="WixUILicenseRtf" Value="License.rtf"' in product
    assert 'WixVariable Id="WixUIBannerBmp" Value="banner.bmp"' in product
    assert 'WixVariable Id="WixUIDialogBmp" Value="dialog.bmp"' in product
    assert "WixUI_Minimal" in product
    assert (
        'BootstrapperApplicationRef Id="WixStandardBootstrapperApplication.RtfLicense"'
        in bundle
    )
    assert 'LicenseFile="License.rtf"' in bundle
    assert 'LogoFile="burn-logo.bmp"' in bundle
    assert "windows_license_rtf.py" in build
    assert "--write" in build
    assert "License.rtf is not RTF" in build
    assert "banner.bmp" in build
    assert "refusing stock WixUI red-CD" in build


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
    """Interactive install starts vc-terminal; uninstall and silent install do not."""
    product = PRODUCT.read_text(encoding="utf-8")
    bundle = BUNDLE.read_text(encoding="utf-8")
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    assert 'Id="LaunchVcTerminal"' in product
    assert "vc-terminal.cmd" in product
    assert 'Directory="INSTALLDIR"' in product
    assert 'ExeCommand="bin\\vc-terminal.cmd"' in product
    assert 'Return="asyncNoWait"' in product
    assert 'After="InstallFinalize"' in product
    assert "LaunchVcTerminal" in product
    # Bare NOT REMOVE would start the GUI on msiexec /qn. Keep uninstall off too.
    assert 'After="InstallFinalize">NOT REMOVE</Custom>' not in product
    launch_line = [
        line
        for line in product.splitlines()
        if "LaunchVcTerminal" in line and "Custom" in line
    ]
    assert launch_line, product
    assert any("NOT REMOVE" in line for line in launch_line)
    seq = [
        line.strip()
        for line in product.splitlines()
        if "LaunchVcTerminal" in line and "After=" in line
    ]
    assert len(seq) == 1, seq
    assert "NOT REMOVE" in seq[0]
    assert "VC_SKIP_TERMINAL_LAUNCH=1" in seq[0]
    assert 'Id="VC_SKIP_TERMINAL_LAUNCH" Secure="yes" Value="0"' in product
    assert 'Id="VC_BURN_UILEVEL" Secure="yes" Value="0"' in product
    assert 'Id="VC_SKIP_TERMINAL_LAUNCH" Secure="yes" Value="1"' not in product
    assert "UILevel&gt;3" in seq[0] or "UILevel>3" in seq[0]
    assert "VC_BURN_UILEVEL&gt;=4" in seq[0] or "VC_BURN_UILEVEL>=4" in seq[0]
    assert 'MsiProperty Name="VC_BURN_UILEVEL" Value="[WixBundleUILevel]"' in bundle
    assert (
        'Name="VC_SKIP_TERMINAL_LAUNCH" Type="string" Value="0" bal:Overridable="yes"'
        in bundle
    )
    assert 'Value="1" bal:Overridable="yes"' not in bundle
    assert (
        'MsiProperty Name="VC_SKIP_TERMINAL_LAUNCH" Value="[VC_SKIP_TERMINAL_LAUNCH]"'
        in bundle
    )
    assert "must gate LaunchVcTerminal" in build
    assert "overridable string defaulting to 0" in build
    assert "must forward VC_SKIP_TERMINAL_LAUNCH into the chained MSI" in build
    assert "WixBundleUILevel" in build
    assert "LaunchVcTerminal" in build
    assert "vc-terminal" in readme.lower()
    assert "VC_SKIP_TERMINAL_LAUNCH" in readme
    assert "UILevel" in readme


def test_windows_installer_updates_per_user_path_not_machine_path() -> None:
    """MSI appends the launcher bin to HKCU PATH and removes it on uninstall."""
    product = PRODUCT.read_text(encoding="utf-8")
    build = BUILD_SCRIPT.read_text(encoding="utf-8")
    readme = README.read_text(encoding="utf-8")
    assert 'Id="LocalAppDataFolder"' in product
    assert 'Id="INSTALLDIR" Name="Vibecrafted"' in product
    env_lines = [line for line in product.splitlines() if "<Environment " in line]
    assert len(env_lines) == 1, env_lines
    blob = env_lines[0]
    assert 'Id="UserLauncherPathEnv"' in blob
    assert 'Name="PATH"' in blob
    assert 'Value="[INSTALLDIR]bin"' in blob
    assert 'System="no"' in blob
    assert 'Part="last"' in blob
    assert 'Action="set"' in blob
    assert 'Permanent="no"' in blob
    assert 'Separator=";"' in blob
    assert 'System="yes"' not in product
    assert 'Root="HKLM"' not in product
    assert 'Id="UserLauncherPath"' in product
    assert "must append the per-user launcher bin" in build
    assert "must not write the machine PATH" in build
    assert "HKCU PATH" in readme
    assert "machine PATH" in readme
    assert "install.ps1" in readme


def test_windows_ci_installs_msi_then_doctor_then_uninstall() -> None:
    """The matrix must install the MSI it just built, then doctor, then remove it.

    Does not rebuild the Runtime Pack. download-artifact stays on the known-good
    v4 SHA. The stable ProductCode is read from Identity.wxi, not minted here.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "install-windows.yml").read_text(
        encoding="utf-8"
    )
    assert "windows-msi-install:" in workflow
    assert "MSI silent install + doctor + uninstall" in workflow
    marker = "\n  windows-msi-install:"
    assert marker in workflow
    msi_job = workflow.split(marker, 1)[1]
    exe_marker = "\n  windows-exe-install:"
    if exe_marker in msi_job:
        msi_job = msi_job.split(exe_marker, 1)[0]
    assert "windows-exe-install:" not in msi_job
    assert "needs: windows-installers" in msi_job
    assert "windows-runtime-pack" not in msi_job
    assert "build-windows-x64-runtime-pack.ps1" not in msi_job
    assert "build-windows-installers.ps1" not in msi_job
    assert "install.ps1" not in msi_job
    assert "taskkill" not in msi_job.lower()
    assert "signtool" not in msi_job.lower()
    assert "VIBECRAFTED_WINDOWS_AUTHENTICODE_THUMBPRINT" not in msi_job
    assert (
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4"
        in msi_job
    )
    assert "d3f86a106a0bac45b974a628896ce1e5585c70a7" not in msi_job
    assert "name: windows-x64-installers" in msi_job
    assert 'InstallScope="perUser"' in msi_job
    assert "LocalAppDataFolder" in msi_job
    assert "ProgramFiles64Folder" in msi_job
    assert STABLE_PRODUCT_CODE in msi_job
    assert "Identity.wxi" in msi_job
    assert "VC_SKIP_TERMINAL_LAUNCH=1" in msi_job
    assert "msiexec.exe" in msi_job
    assert "cmd.exe" not in msi_job
    assert 'Arguments = "/i' in msi_job
    assert 'Arguments = "/x' in msi_job
    assert "UseShellExecute = $false" in msi_job
    assert msi_job.count("WaitForExit()") >= 2
    assert "/qn" in msi_job
    assert "/l*v" in msi_job
    assert "Skipping action: LaunchVcTerminal" in msi_job
    assert "Doing action: LaunchVcTerminal" in msi_job
    assert 'vibecrafted.cmd" doctor' in msi_job or "vibecrafted.cmd" in msi_job
    assert "doctor" in msi_job
    assert "DOCTOR_SUMMARY=" in msi_job
    assert "& $cmd doctor" in msi_job
    assert "{$stable}" in msi_job
    assert f'$stable = "{STABLE_PRODUCT_CODE}"' in msi_job
    assert "/x" in msi_job
    assert "uninstall left residue under $vcHome" in msi_job
    assert "expected exactly one user PATH entry" in msi_job
    assert "uninstall left user PATH entry" in msi_job
    assert "machine PATH changed" in msi_job
    assert "windows-x64-msi-install-logs" in msi_job


def test_windows_ci_installs_exe_then_doctor_then_uninstall() -> None:
    """The matrix must run the unsigned Burn EXE it just built, then doctor, then remove it.

    Separate windows-latest job from the MSI install so the shared ProductCode
    does not collide. Does not rebuild the Runtime Pack. download-artifact stays
    on the known-good v4 SHA. Quiet switches are the ones WiX 3.14's engine and
    this RtfLicense BA honor; ``/install`` is not one of them.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "install-windows.yml").read_text(
        encoding="utf-8"
    )
    assert "windows-exe-install:" in workflow
    assert "EXE silent install + doctor + uninstall" in workflow
    marker = "\n  windows-exe-install:"
    assert marker in workflow
    exe_job = workflow.split(marker, 1)[1]
    assert "windows-msi-install:" not in exe_job
    assert "needs: windows-installers" in exe_job
    assert "runs-on: windows-latest" in exe_job
    assert "windows-runtime-pack" not in exe_job
    assert "build-windows-x64-runtime-pack.ps1" not in exe_job
    assert "build-windows-installers.ps1" not in exe_job
    assert "install.ps1" not in exe_job
    assert "msiexec" not in exe_job.lower()
    assert "taskkill" not in exe_job.lower()
    assert "signtool" not in exe_job.lower()
    assert "cmd.exe" not in exe_job
    assert "VIBECRAFTED_WINDOWS_AUTHENTICODE_THUMBPRINT" not in exe_job
    assert (
        "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4"
        in exe_job
    )
    assert "d3f86a106a0bac45b974a628896ce1e5585c70a7" not in exe_job
    assert "name: windows-x64-installers" in exe_job
    assert 'InstallScope="perUser"' in exe_job
    assert "LocalAppDataFolder" in exe_job
    assert "ProgramFiles64Folder" in exe_job
    assert STABLE_PRODUCT_CODE in exe_job
    assert "Identity.wxi" in exe_job
    assert 'bal:Overridable="yes"' in exe_job
    assert "VC_SKIP_TERMINAL_LAUNCH=1" in exe_job
    assert "Do not pass /install" in exe_job
    assert "UseShellExecute = $false" in exe_job
    assert exe_job.count("WaitForExit()") >= 2
    assert "Unblock-File" in exe_job
    argument_lines = [
        line.strip()
        for line in exe_job.splitlines()
        if "$psi.Arguments" in line
    ]
    assert len(argument_lines) == 2, argument_lines
    install_args = argument_lines[0]
    uninstall_args = argument_lines[1]
    assert install_args == (
        '$psi.Arguments = "/quiet /norestart /log `"$installLog`" VC_SKIP_TERMINAL_LAUNCH=1"'
    )
    assert "/install" not in install_args
    assert uninstall_args == (
        '$psi.Arguments = "/uninstall /quiet /norestart /log `"$uninstallLog`""'
    )
    assert "WixBundleUILevel" in exe_job
    assert "Variable: WixBundleUILevel = 2" in exe_job
    assert "Variable: WixBundleUILevel = 4" in exe_job
    assert "Skipping action: LaunchVcTerminal" in exe_job
    assert "Doing action: LaunchVcTerminal" in exe_job
    assert "quiet EXE install did not record WixBundleUILevel 2" in exe_job
    assert "Burn did not set VC_SKIP_TERMINAL_LAUNCH to 1" in exe_job
    assert "Applied execute package: VibecraftedRuntimePackMsi" in exe_job
    assert "action: Uninstall" in exe_job
    assert "& $cmd doctor" in exe_job
    assert "DOCTOR_SUMMARY=" in exe_job
    assert "expected exactly one user PATH entry" in exe_job
    assert "uninstall left user PATH entry" in exe_job
    assert "uninstall left residue under $vcHome" in exe_job
    assert "machine PATH changed" in exe_job
    assert "windows-x64-exe-install-logs" in exe_job
    assert "EXE_INSTALL_COMMAND=" in exe_job
    assert "EXE_UNINSTALL_COMMAND=" in exe_job
