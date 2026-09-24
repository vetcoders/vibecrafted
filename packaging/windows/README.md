# Windows EXE / MSI installers (portable Runtime Pack)

Thin adapters over `scripts/install-runtime-pack.ps1`. One product identity
(`Identity.wxi`: name, VERSION-driven ProductVersion, stable UpgradeCode) drives
both the MSI (`Product.wxs`) and the Burn EXE (`Bundle.wxs`).

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
