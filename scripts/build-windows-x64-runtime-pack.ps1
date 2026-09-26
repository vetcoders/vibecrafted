#Requires -Version 5.1
<#
.SYNOPSIS
    Build the Windows x64 Runtime Pack from this commit.

.DESCRIPTION
    Builder-only: may use cargo, npm, uv, Git Bash. The produced carrier is
    prebuilt; a customer install must not need those tools.
#>
[CmdletBinding()]
param(
    [string]$Output = "",
    [string]$SourceRevision = $env:VIBECRAFTED_SOURCE_REVISION
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Die([string]$Message) {
    Write-Error "Windows x64 Runtime Pack build failed: $Message"
    exit 1
}

if ($env:OS -notmatch "Windows") { Die "builder must run natively on Windows" }
if ($env:PROCESSOR_ARCHITECTURE -match "ARM64") { Die "this builder is x64-only" }

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $SourceRevision) {
    $SourceRevision = (& git -C $repoRoot rev-parse HEAD).Trim()
}
if ($SourceRevision -notmatch '^[0-9a-f]{40}$') {
    Die "source revision must be a full Git SHA"
}
$version = (Get-Content -LiteralPath (Join-Path $repoRoot "VERSION") -Raw).Trim()
# Same donor revisions the Linux assembler builds. Provenance records these
# only after this Windows builder actually compiles them (fail closed below).
$terminalRevision = "d6685ead9018ad89411291d6198476666e48b0f8"
$terminalArchiveSha256 = "3cd6670c4a80c589b945ed1b45c1f033c80745ceb34d3466e9476a1c3eeb0f71"
$frameRevision = "7ab84069c9b7994ce0b705ccedd708aa3a35dcb6"
$frameArchiveSha256 = "55851e094b91d3b41712edcdc66d69f97da5859118395fee497bb104714b125c"
if (-not $Output) {
    $Output = Join-Path $repoRoot "build\Vibecrafted_RuntimePack_${version}-win32-x64.tar.gz"
}

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("vc-win-pack-" + [guid]::NewGuid().ToString("N"))
$payload = Join-Path $work "payload"
New-Item -ItemType Directory -Path (Join-Path $payload "bin") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $payload "scripts") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $payload "libexec") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $payload "server\site") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $payload "config") | Out-Null

function Copy-Tree($Source, $Destination) {
    if (-not (Test-Path -LiteralPath $Source)) { return }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Copy-Item -Path (Join-Path $Source "*") -Destination $Destination -Recurse -Force
}

Set-Content -LiteralPath (Join-Path $payload "VERSION") -Value "$version`n" -Encoding ascii
Copy-Item (Join-Path $repoRoot "scripts\vetcoders_install.py") (Join-Path $payload "scripts\vetcoders_install.py")
Copy-Item (Join-Path $repoRoot "scripts\distribution_manifest.py") (Join-Path $payload "scripts\distribution_manifest.py")
Copy-Item (Join-Path $repoRoot "scripts\installer_brand.py") (Join-Path $payload "scripts\installer_brand.py")
Copy-Item (Join-Path $repoRoot "scripts\install-runtime-pack.ps1") (Join-Path $payload "scripts\install-runtime-pack.ps1")
if (Test-Path (Join-Path $repoRoot "scripts\vibecrafted")) {
    Copy-Item (Join-Path $repoRoot "scripts\vibecrafted") (Join-Path $payload "scripts\vibecrafted")
}
Copy-Tree (Join-Path $repoRoot "vibecrafted-core\vibecrafted_core") (Join-Path $payload "vibecrafted-core\vibecrafted_core")
$stamped = "{0}+g{1}" -f $version, $SourceRevision.Substring(0, 8)
Set-Content -LiteralPath (Join-Path $payload "vibecrafted-core\vibecrafted_core\VERSION") -Value "$stamped`n" -Encoding ascii
if (Test-Path (Join-Path $repoRoot "config")) {
    Copy-Tree (Join-Path $repoRoot "config") (Join-Path $payload "config")
}

& python (Join-Path $repoRoot "scripts\distribution_manifest.py") carrier `
    --source $repoRoot --output (Join-Path $payload "source-provenance.json") `
    --owner-repo vetcoders/vibecrafted --source-revision $SourceRevision
if ($LASTEXITCODE -ne 0) { Die "source provenance failed" }

$shim = Join-Path $work "shim"
New-Item -ItemType Directory -Path $shim | Out-Null
Set-Content -LiteralPath (Join-Path $shim "python3.cmd") "@echo off`r`npython %*`r`n" -Encoding ascii
$hostPython = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($hostPython) {
    Copy-Item $hostPython (Join-Path $shim "python3.exe")
}
$env:PATH = "$shim;$env:PATH"
# Loctree, AICX, PRView and ScreenScribe ship through their own channels
# (npm / GitHub releases / PyPI); this pack never carries them.

function Install-CargoBin([string]$PackagePath, [string]$BinName, [string]$DestName) {
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { return $false }
    if (-not (Test-Path -LiteralPath $PackagePath)) { return $false }
    $target = Join-Path $work "$DestName-target"
    & cargo build --locked --manifest-path $PackagePath --release --bin $BinName
    if ($LASTEXITCODE -ne 0) { return $false }
    $built = Join-Path (Split-Path $PackagePath) "target\release\$BinName.exe"
    if (-not (Test-Path $built)) {
        $built = Join-Path $repoRoot "target\release\$BinName.exe"
    }
    if (-not (Test-Path $built)) { return $false }
    Copy-Item $built (Join-Path $payload "bin\$DestName.exe")
    return $true
}

$serverExe = $null
$cargoLeptos = Get-Command cargo-leptos -ErrorAction SilentlyContinue
$serverDir = Join-Path $repoRoot "vibecrafted-server"
if ($cargoLeptos) {
    Push-Location $serverDir
    try {
        $env:LEPTOS_SITE_ROOT = Join-Path $payload "server\site"
        & cargo leptos build --release --bin-cargo-args="--locked" --lib-cargo-args="--locked"
        if ($LASTEXITCODE -eq 0) {
            $candidate = Join-Path $serverDir "target\release\vibecrafted-server-web.exe"
            if (Test-Path $candidate) { $serverExe = $candidate }
        }
    }
    finally { Pop-Location }
}
if (-not $serverExe -and (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Push-Location $serverDir
    try {
        & cargo build --locked -p vibecrafted-server-web --features ssr --release
        if ($LASTEXITCODE -eq 0) {
            $candidate = Join-Path $serverDir "target\release\vibecrafted-server-web.exe"
            if (Test-Path $candidate) { $serverExe = $candidate }
        }
    }
    finally { Pop-Location }
}
if ($serverExe) {
    Copy-Item $serverExe (Join-Path $payload "bin\vc-server.exe")
}

$controlCoreToml = Join-Path $repoRoot "vibecrafted-server\control-core\Cargo.toml"
Install-CargoBin $controlCoreToml "scaffold-doctor" "scaffold-doctor" | Out-Null
Install-CargoBin $controlCoreToml "control-observe" "control-observe" | Out-Null

function Get-Sha256File([string]$Path) {
    return ([BitConverter]::ToString(
        [System.Security.Cryptography.SHA256]::Create().ComputeHash(
            [System.IO.File]::ReadAllBytes($Path)
        )
    ) -replace '-', '').ToLowerInvariant()
}

function Fetch-DonorSource([string]$Url, [string]$ExpectedSha256, [string]$Archive, [string]$Destination) {
    Write-Host "Fetching donor: $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Archive
    $actual = Get-Sha256File $Archive
    if ($actual -ne $ExpectedSha256.ToLowerInvariant()) {
        Die "donor archive checksum mismatch for $Url (got $actual expected $ExpectedSha256)"
    }
    if (Test-Path -LiteralPath $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    & tar -xzf $Archive -C $Destination --strip-components=1
    if ($LASTEXITCODE -ne 0) {
        # Windows tar may lack --strip-components; fall back to nested extract.
        $nestedRoot = Join-Path $work ("donor-extract-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $nestedRoot -Force | Out-Null
        & tar -xzf $Archive -C $nestedRoot
        if ($LASTEXITCODE -ne 0) { Die "donor archive extract failed: $Archive" }
        $inner = Get-ChildItem -LiteralPath $nestedRoot -Directory | Select-Object -First 1
        if (-not $inner) { Die "donor archive produced no directory: $Archive" }
        Get-ChildItem -LiteralPath $inner.FullName -Force | ForEach-Object {
            Move-Item -LiteralPath $_.FullName -Destination (Join-Path $Destination $_.Name) -Force
        }
        Remove-Item -LiteralPath $nestedRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
    Die "cargo is required to build Windows vc-terminal and vc-frame"
}
if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
    Die "tar is required to extract vc-terminal and vc-frame donor archives"
}

# vc-frame's prost-build needs protoc on PATH (fail closed with a clear hint).
if (-not $env:PROTOC -or -not (Test-Path -LiteralPath $env:PROTOC -PathType Leaf)) {
    $protocCmd = Get-Command protoc -ErrorAction SilentlyContinue
    if ($protocCmd) {
        $env:PROTOC = $protocCmd.Source
    }
    else {
        $protocVersion = "29.3"
        $protocZip = Join-Path $work "protoc-$protocVersion-win64.zip"
        $protocRoot = Join-Path $work "protoc-$protocVersion"
        $protocUrl = "https://github.com/protocolbuffers/protobuf/releases/download/v$protocVersion/protoc-$protocVersion-win64.zip"
        Write-Host "Fetching protoc $protocVersion for vc-frame prost-build"
        Invoke-WebRequest -Uri $protocUrl -OutFile $protocZip
        if (Test-Path -LiteralPath $protocRoot) {
            Remove-Item -LiteralPath $protocRoot -Recurse -Force
        }
        Expand-Archive -LiteralPath $protocZip -DestinationPath $protocRoot -Force
        $env:PROTOC = Join-Path $protocRoot "bin\protoc.exe"
        if (-not (Test-Path -LiteralPath $env:PROTOC -PathType Leaf)) {
            Die "protoc download did not produce $env:PROTOC"
        }
        $env:PATH = "$(Join-Path $protocRoot 'bin');$env:PATH"
    }
}
& $env:PROTOC --version | Out-Null
if ($LASTEXITCODE -ne 0) { Die "protoc is required to build vc-frame on Windows" }

# openssl-sys (via isahc native-tls) needs a real OpenSSL on Windows. Prefer a
# host install; never silently skip vc-frame. Vendored openssl-src failed on
# this host without OPENSSL_DIR.
if (-not $env:OPENSSL_DIR) {
    $opensslCandidates = @(
        "C:\Program Files\OpenSSL-Win64",
        "C:\Program Files\OpenSSL",
        (Join-Path ${env:ProgramFiles} "OpenSSL-Win64")
    )
    foreach ($candidate in $opensslCandidates) {
        if ($candidate -and (Test-Path -LiteralPath (Join-Path $candidate "include\openssl\ssl.h"))) {
            $env:OPENSSL_DIR = $candidate
            break
        }
    }
}
if (-not $env:OPENSSL_DIR) {
    Die "OPENSSL_DIR is required to build vc-frame on Windows (install OpenSSL-Win64 or set OPENSSL_DIR)"
}
if (-not $env:OPENSSL_LIB_DIR) {
    $libMd = Join-Path $env:OPENSSL_DIR "lib\VC\x64\MD"
    if (Test-Path -LiteralPath $libMd) {
        $env:OPENSSL_LIB_DIR = $libMd
    }
}
if (-not $env:OPENSSL_INCLUDE_DIR) {
    $inc = Join-Path $env:OPENSSL_DIR "include"
    if (Test-Path -LiteralPath $inc) {
        $env:OPENSSL_INCLUDE_DIR = $inc
    }
}
$env:OPENSSL_NO_VENDOR = "1"
Write-Host "Using OPENSSL_DIR=$env:OPENSSL_DIR"

$terminalSrc = Join-Path $work "vc-terminal"
$frameSrc = Join-Path $work "vc-frame"
Fetch-DonorSource `
    "https://codeload.github.com/vetcoders/vc-terminal/tar.gz/$terminalRevision" `
    $terminalArchiveSha256 `
    (Join-Path $work "vc-terminal.tar.gz") `
    $terminalSrc
Fetch-DonorSource `
    "https://codeload.github.com/vetcoders/vc-frame/tar.gz/$frameRevision" `
    $frameArchiveSha256 `
    (Join-Path $work "vc-frame.tar.gz") `
    $frameSrc

Write-Host "Building vc-terminal (alacritty) for win32-x64 at $terminalRevision"
Push-Location $terminalSrc
try {
    & cargo build --release --bin alacritty
    if ($LASTEXITCODE -ne 0) { Die "vc-terminal cargo build failed" }
}
finally { Pop-Location }
$terminalBuilt = Join-Path $terminalSrc "target\release\alacritty.exe"
if (-not (Test-Path -LiteralPath $terminalBuilt -PathType Leaf)) {
    Die "vc-terminal release binary missing: $terminalBuilt"
}

Write-Host "Building vc-frame for win32-x64 at $frameRevision"
Push-Location $frameSrc
try {
    $env:VC_FRAME_GIT_SHA = $frameRevision
    $env:VC_FRAME_GIT_DIRTY = "0"
    & cargo xtask build --release
    if ($LASTEXITCODE -ne 0) { Die "vc-frame cargo xtask build failed" }
}
finally { Pop-Location }
$frameBuilt = Join-Path $frameSrc "target\release\vc-frame.exe"
if (-not (Test-Path -LiteralPath $frameBuilt -PathType Leaf)) {
    Die "vc-frame release binary missing: $frameBuilt"
}

New-Item -ItemType Directory -Path (Join-Path $payload "libexec") -Force | Out-Null
Copy-Item $terminalBuilt (Join-Path $payload "libexec\vc-terminal.exe")
Copy-Item $frameBuilt (Join-Path $payload "libexec\vc-frame.exe")
# Ship the OpenSSL shared libraries next to vc-frame.exe so the frame starts on
# hosts that do not have OpenSSL-Win64 on PATH (link against MD import libs).
foreach ($dllName in @("libssl-3-x64.dll", "libcrypto-3-x64.dll")) {
    $dllSrc = Join-Path $env:OPENSSL_DIR $dllName
    if (-not (Test-Path -LiteralPath $dllSrc -PathType Leaf)) {
        $dllSrc = Join-Path $env:OPENSSL_DIR "bin\$dllName"
    }
    if (-not (Test-Path -LiteralPath $dllSrc -PathType Leaf)) {
        Die "OpenSSL shared library missing beside OPENSSL_DIR: $dllName"
    }
    Copy-Item $dllSrc (Join-Path $payload "libexec\$dllName")
}
$termEntry = Join-Path $repoRoot "scripts\vc-terminal-product-entry.cmd"
$frameEntry = Join-Path $repoRoot "scripts\vc-frame-product-entry.cmd"
if (-not (Test-Path -LiteralPath $termEntry -PathType Leaf)) {
    Die "missing Windows terminal product entry: $termEntry"
}
if (-not (Test-Path -LiteralPath $frameEntry -PathType Leaf)) {
    Die "missing Windows frame product entry: $frameEntry"
}
Copy-Item $termEntry (Join-Path $payload "scripts\vc-terminal-product-entry.cmd")
Copy-Item $frameEntry (Join-Path $payload "scripts\vc-frame-product-entry.cmd")
Copy-Item $termEntry (Join-Path $payload "bin\vc-terminal.cmd")
Copy-Item $frameEntry (Join-Path $payload "bin\vc-frame.cmd")
# Complete frame surface: product config ships with vibecrafted-core; fail closed
# if the canonical tree is absent so a terminal without frame config cannot ship.
$frameConfig = Join-Path $payload "vibecrafted-core\vibecrafted_core\config\vc-frame\config.kdl"
if (-not (Test-Path -LiteralPath $frameConfig -PathType Leaf)) {
    Die "complete vc-frame config missing from payload: $frameConfig"
}
Write-Host "Windows vc-terminal + vc-frame staged under libexec/ and bin/"

$embedZip = Join-Path $work "python-embed.zip"
$embedUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
Invoke-WebRequest -Uri $embedUrl -OutFile $embedZip
$binDir = Join-Path $payload "bin"
Expand-Archive -LiteralPath $embedZip -DestinationPath $binDir -Force
$wrapper = Join-Path $binDir "python.exe"
if (-not (Test-Path -LiteralPath $wrapper)) {
    Die "embeddable CPython did not provide bin/python.exe"
}
$pythonDll = Get-ChildItem -LiteralPath $binDir -Filter "python3*.dll" -File | Select-Object -First 1
if (-not $pythonDll) {
    Die "embeddable CPython is missing python3xx.dll next to python.exe; refusing to ship a stub"
}
$zipName = (Get-ChildItem -LiteralPath $binDir -Filter "python*.zip" -File | Select-Object -First 1).Name
if (-not $zipName) { $zipName = "python312.zip" }
$pth = Get-ChildItem -LiteralPath $binDir -Filter "python*._pth" -File | Select-Object -First 1
if (-not $pth) { Die "embeddable CPython is missing ._pth" }
$pthBody = @"
$zipName
.
..\python-site
..\vibecrafted-core
..\scripts
import site
"@
Set-Content -LiteralPath $pth.FullName -Value $pthBody -Encoding ascii
& $wrapper -c "import sys; print(sys.version)"
if ($LASTEXITCODE -ne 0) { Die "pack python.exe does not start" }

New-Item -ItemType Directory -Path (Join-Path $payload "python-site") -Force | Out-Null
$site = Join-Path $payload "python-site"
$pipTmp = Join-Path $work "pip-tmp"
New-Item -ItemType Directory -Path $pipTmp -Force | Out-Null
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$env:TEMP = $pipTmp
$env:TMP = $pipTmp
$siteInstalled = $false
for ($attempt = 1; $attempt -le 6; $attempt++) {
    & python -m pip install --disable-pip-version-check --upgrade --target $site `
        "jsonschema>=4.23,<5" "PyYAML>=6.0,<7"
    if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath (Join-Path $site "jsonschema"))) {
        $siteInstalled = $true
        break
    }
    Write-Host "python-site install attempt $attempt failed; retrying"
    Start-Sleep -Seconds (2 * $attempt)
}
$env:TEMP = $previousTemp
$env:TMP = $previousTmp
if (-not $siteInstalled) {
    Die "python-site jsonschema install failed"
}
& $wrapper -c "import jsonschema, yaml; print('jsonschema', jsonschema.__version__)"
if ($LASTEXITCODE -ne 0) {
    Die "pack python cannot import jsonschema from python-site"
}
$launcher = @"
@echo off
setlocal EnableExtensions
set "BIN_DIR=%~dp0"
for %%I in ("%BIN_DIR%..") do set "VIBECRAFTED_RUNTIME_ROOT=%%~fI"
set "PYTHONPATH=%VIBECRAFTED_RUNTIME_ROOT%\vibecrafted-core;%VIBECRAFTED_RUNTIME_ROOT%\python-site"
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
"%BIN_DIR%python.exe" -m vibecrafted_core.cli %*
"@
Set-Content -LiteralPath (Join-Path $payload "bin\vibecrafted.cmd") -Value $launcher -Encoding ascii

& python (Join-Path $repoRoot "scripts\render-python-entrypoint-launchers.py") `
    --pyproject (Join-Path $repoRoot "vibecrafted-core\pyproject.toml") `
    --bin-dir (Join-Path $payload "bin") --windows

$inventoryScript = Join-Path $work "write_inventory.py"
@'
import hashlib, json, os, subprocess, sys
from pathlib import Path

root = Path(os.environ["PAYLOAD"])
source_revision = os.environ["SOURCE_REVISION"]
terminal_revision = os.environ["TERMINAL_REVISION"]
frame_revision = os.environ["FRAME_REVISION"]
source_manifest_sha = hashlib.sha256((root / "source-provenance.json").read_bytes()).hexdigest()
mandatory = ["python", "vc-server", "vc-terminal", "vc-frame"]
optional = {
    "voc": "limited-platform-scope",
    "vc-start": "limited-platform-scope",
    "vc-server-supervisor": "limited-platform-scope",
}
def exe_path(name):
    if name == "python":
        return root / "bin" / "python.exe"
    if name in {"vc-terminal", "vc-frame"}:
        cmd = root / "bin" / f"{name}.cmd"
        if cmd.is_file():
            return cmd
        return root / "bin" / f"{name}.exe"
    return root / "bin" / f"{name}.exe"
records = []
unsupported = []
donor_revisions = {"vc-terminal": terminal_revision, "vc-frame": frame_revision}
donor_urls = {
    "vc-terminal": f"https://github.com/vetcoders/vc-terminal/tree/{terminal_revision}",
    "vc-frame": f"https://github.com/vetcoders/vc-frame/tree/{frame_revision}",
}
for name in mandatory:
    path = exe_path(name)
    if not path.is_file():
        print(f"missing mandatory executable: {path}", file=sys.stderr)
        sys.exit(1)
    # Native hosts must exist beside the product wrappers.
    if name == "vc-terminal" and not (root / "libexec" / "vc-terminal.exe").is_file():
        print("missing mandatory libexec/vc-terminal.exe", file=sys.stderr)
        sys.exit(1)
    if name == "vc-frame" and not (root / "libexec" / "vc-frame.exe").is_file():
        print("missing mandatory libexec/vc-frame.exe", file=sys.stderr)
        sys.exit(1)
    argv = ["--version"]
    try:
        output = subprocess.run([str(path), *argv], text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=30, check=True).stdout.strip().splitlines()[0]
    except Exception as exc:
        output = f"unversioned ({exc.__class__.__name__})"
    records.append({
        "name": name, "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "version_argv": argv, "version_output": output,
        "source_url": donor_urls.get(name, "https://github.com/vetcoders/vibecrafted"),
        "source_revision": donor_revisions.get(name, source_revision),
        "source_archive_sha256": source_manifest_sha,
        "target": "x86_64-pc-windows-msvc", "license": "MIT",
    })
reasons = {
    "voc": "voc is not built for Windows in this pack",
    "vc-start": "vc-start is not built for Windows in this pack",
    "vc-server-supervisor": "launchd supervisor is macOS-only; Windows uses vibecrafted server",
}
for name, classification in optional.items():
    path = exe_path(name)
    if path.is_file():
        argv = ["--version"]
        try:
            output = subprocess.run([str(path), *argv], text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=30, check=True).stdout.strip().splitlines()[0]
        except Exception as exc:
            output = f"unversioned ({exc.__class__.__name__})"
        records.append({
            "name": name, "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "version_argv": argv, "version_output": output,
            "source_url": "https://github.com/vetcoders/vibecrafted",
            "source_revision": source_revision, "source_archive_sha256": source_manifest_sha,
            "target": "x86_64-pc-windows-msvc", "license": "MIT",
        })
    else:
        unsupported.append({
            "name": name,
            "classification": classification,
            "reason": reasons[name],
        })
manifest = {
    "schema": "io.vetcoders.vibecrafted.runtime-inventory.v1",
    "platform": "win32-x64",
    "architecture": "x64",
    "executables": records,
    "unsupported": unsupported,
}
(root / "runtime-inventory.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
'@ | Set-Content -LiteralPath $inventoryScript -Encoding utf8
$env:PAYLOAD = $payload
$env:SOURCE_REVISION = $SourceRevision
$env:TERMINAL_REVISION = $terminalRevision
$env:FRAME_REVISION = $frameRevision
& python $inventoryScript
if ($LASTEXITCODE -ne 0) { Die "inventory closed with missing mandatory payload" }

& powershell -NoLogo -NoProfile -File (Join-Path $repoRoot "scripts\package-runtime-pack.ps1") `
    -PayloadRoot $payload -Output $Output `
    -SourceRevision $SourceRevision -TerminalRevision $terminalRevision `
    -FrameRevision $frameRevision -Version $version
if ($LASTEXITCODE -ne 0) { Die "packaging failed" }

$signKey = $env:VIBECRAFTED_SIGNING_KEY
$productKeyHint = Join-Path $env:USERPROFILE ".keys\vibecrafted-signing.key"
if (-not $signKey -and (Test-Path $productKeyHint)) { $signKey = $productKeyHint }
$sig = "$Output.sig"
$verdict = "unsigned"
if ($signKey -and (Get-Command openssl -ErrorAction SilentlyContinue)) {
    & openssl dgst -sha256 -sign $signKey -out $sig $Output
    if ($LASTEXITCODE -eq 0) { $verdict = "signed-with-provided-key" }
}
if ($verdict -eq "unsigned") {
    $rehearsal = Join-Path $work "rehearsal.key"
    if (Get-Command openssl -ErrorAction SilentlyContinue) {
        & openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out $rehearsal
        & openssl dgst -sha256 -sign $rehearsal -out $sig $Output
        Copy-Item $rehearsal "$Output.rehearsal.key"
        & openssl pkey -in $rehearsal -pubout -out "$Output.rehearsal.pub"
        $verdict = "rehearsal-signature-only; release operator must re-sign with vibecrafted-signing-v1"
    }
    else {
        Die "openssl is required to detach-sign the Runtime Pack"
    }
}
Write-Host "Windows Runtime Pack: $Output"
Write-Host "Signature verdict: $verdict"
Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
