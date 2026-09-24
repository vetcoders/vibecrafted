# Windows EXE / MSI installers (portable Runtime Pack)

Thin adapters over `scripts/install-runtime-pack.ps1`. One product identity
(`Identity.wxi`: name, VERSION-driven ProductVersion, stable UpgradeCode) drives
both the MSI (`Product.wxs`) and the Burn EXE (`Bundle.wxs`).

Per-user portable MSI: `InstallScope=perUser`, payload under `LocalAppDataFolder`
(`%LOCALAPPDATA%\Vibecrafted\Installer`). MajorUpgrade uses the stable
UpgradeCode; same-version upgrades are refused. Uninstall runs only when
`REMOVE=ALL` and fails closed (`Return=check`).

License and publisher identity come from the repo `LICENSE` (BUSL-1.1,
Licensor Libraxis AI Sp. z o.o.) via `packaging/windows/License.rtf`. MSI shows
`WixUI_Minimal` + `WixUILicenseRtf`; Burn uses `RtfLicense` with `LicenseFile`.
ARP help link points at `SECURITY.md` (`hello@vetcoders.io`). After a successful
install the MSI launches `vc-terminal.cmd` (frame-backed); launch never runs on
uninstall. Pack `.sig` + `vibecrafted-signing-v1.pub` is Runtime Pack signature
evidence, not Authenticode.

Limit for this cut: the voc radio is not in this installer cut because tokio's
Unix socket types are `cfg(unix)` and this cut does not switch mux-agent to a
Windows AF_UNIX transport.

Build (requires an existing pack tarball + `.sha256` + `.sig`):

```powershell
powershell -NoProfile -File .\scripts\build-windows-installers.ps1 `
  -Pack .\build\Vibecrafted_RuntimePack_<version>-win32-x64.tar.gz
```

WiX 3.14 binaries are fetched into `packaging/windows/.cache` (gitignored).
Missing `-Pack` fails closed. This script does not install into the operator
`%LOCALAPPDATA%\Vibecrafted`.
