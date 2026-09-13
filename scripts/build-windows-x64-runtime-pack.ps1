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
$terminalRevision = "d6685ead9018ad89411291d6198476666e48b0f8"
$frameRevision = "7ab84069c9b7994ce0b705ccedd708aa3a35dcb6"
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
# Do not run stage-runtime-foundations.sh here. Git Bash compiles aicx/llama
# against POSIX paths and needs libclang; this Windows builder copies host
# cargo bins and inventories missing optional tools honestly.
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
foreach ($name in @("loct", "loctree", "loctree-mcp", "loctree-lsp", "aicx", "aicx-mcp", "prview")) {
    $dest = Join-Path $payload "bin\$name.exe"
    if (-not (Test-Path -LiteralPath $dest)) {
        $src = Join-Path $cargoBin "$name.exe"
        if (Test-Path -LiteralPath $src) { Copy-Item $src $dest }
    }
}

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
        "jsonschema>=4.23,<5" "PyYAML>=6.0,<7" "screenscribe==0.1.19"
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
$screenscribeCmd = @"
@echo off
setlocal EnableExtensions
set "BIN_DIR=%~dp0"
for %%I in ("%BIN_DIR%..") do set "VIBECRAFTED_RUNTIME_ROOT=%%~fI"
set "PYTHONPATH=%VIBECRAFTED_RUNTIME_ROOT%\python-site;%VIBECRAFTED_RUNTIME_ROOT%\vibecrafted-core"
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
"%BIN_DIR%python.exe" -c "from screenscribe.bootstrap import main; main()" %*
"@
Set-Content -LiteralPath (Join-Path $binDir "screenscribe.cmd") -Value $screenscribeCmd -Encoding ascii

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

$screenscribeSite = Join-Path $payload "python-site\screenscribe"
if (Test-Path -LiteralPath $screenscribeSite) {
    $screenscribeCmd = @'
@echo off
setlocal EnableExtensions
set "BIN_DIR=%~dp0"
for %%I in ("%BIN_DIR%..") do set "VIBECRAFTED_RUNTIME_ROOT=%%~fI"
set "PYTHONPATH=%VIBECRAFTED_RUNTIME_ROOT%\python-site;%VIBECRAFTED_RUNTIME_ROOT%\vibecrafted-core"
set "PYTHONNOUSERSITE=1"
set "PYTHONDONTWRITEBYTECODE=1"
"%BIN_DIR%python.exe" -c "from screenscribe.bootstrap import main; main()" %*
'@
    Set-Content -LiteralPath (Join-Path $binDir "screenscribe.cmd") -Value $screenscribeCmd -Encoding ascii
    if (-not (Test-Path -LiteralPath (Join-Path $binDir "screenscribe.cmd"))) {
        Die "screenscribe wheel is present but bin/screenscribe.cmd was not written"
    }
}

$inventoryScript = Join-Path $work "write_inventory.py"
@'
import hashlib, json, os, subprocess, sys
from pathlib import Path

root = Path(os.environ["PAYLOAD"])
source_revision = os.environ["SOURCE_REVISION"]
terminal_revision = os.environ["TERMINAL_REVISION"]
frame_revision = os.environ["FRAME_REVISION"]
source_manifest_sha = hashlib.sha256((root / "source-provenance.json").read_bytes()).hexdigest()
mandatory = ["python", "loct", "loctree", "loctree-mcp", "loctree-lsp", "aicx", "aicx-mcp", "vc-server"]
optional = {
    "prview": "release-blocker",
    "screenscribe": "release-blocker",
    "voc": "limited-platform-scope",
    "vc-start": "limited-platform-scope",
    "vc-frame": "limited-platform-scope",
    "vc-terminal": "limited-platform-scope",
    "vc-server-supervisor": "limited-platform-scope",
}
def exe_path(name):
    if name == "python":
        return root / "bin" / "python.exe"
    if name == "screenscribe":
        cmd = root / "bin" / "screenscribe.cmd"
        if cmd.is_file():
            return cmd
        return root / "bin" / "screenscribe.exe"
    return root / "bin" / f"{name}.exe"
records = []
unsupported = []
for name in mandatory:
    path = exe_path(name)
    if not path.is_file():
        print(f"missing mandatory executable: {path}", file=sys.stderr)
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
        "source_url": "https://github.com/vetcoders/vibecrafted",
        "source_revision": source_revision, "source_archive_sha256": source_manifest_sha,
        "target": "x86_64-pc-windows-msvc", "license": "MIT",
    })
reasons = {
    "vc-frame": "no supported Windows vc-frame binary in this pack",
    "vc-terminal": "no supported Windows vc-terminal binary in this pack",
    "voc": "voc is not built for Windows in this pack",
    "vc-start": "vc-start is not built for Windows in this pack",
    "prview": "prview has no Windows artifact in this pack",
    "screenscribe": "screenscribe has no Windows artifact in this pack",
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
