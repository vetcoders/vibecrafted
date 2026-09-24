#Requires -Version 5.1
<#
.SYNOPSIS
    Build the Windows EXE (Burn) and MSI installers for one Runtime Pack.

.DESCRIPTION
    Thin adapters over scripts/install-runtime-pack.ps1. Fetches WiX Toolset
    3.14 binaries into packaging/windows/.cache (uncommitted). Fails closed when
    -Pack is missing. Does not install into the operator %LOCALAPPDATA%\Vibecrafted.
    Limit: the voc radio is not in this installer cut because tokio's Unix socket
    types are cfg(unix) and this cut does not switch mux-agent to a Windows AF_UNIX
    transport.

.PARAMETER Pack
    Path to the win32-x64 Runtime Pack .tar.gz. Required; must exist with .sha256 and .sig.

.PARAMETER OutDir
    Directory for Vibecrafted.msi and Vibecrafted.exe. Default: packaging/windows/out.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Pack,
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Die([string]$Message) {
    Write-Error "Windows installer build failed: $Message"
    exit 1
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$packagingRoot = Join-Path $repoRoot "packaging\windows"
$cacheRoot = Join-Path $packagingRoot ".cache\wix314"
$stagingRoot = Join-Path $packagingRoot "staging"
$wixObjRoot = Join-Path $packagingRoot ".cache\obj"
if (-not $OutDir) { $OutDir = Join-Path $packagingRoot "out" }

if (-not (Test-Path -LiteralPath $Pack)) {
    Die "Runtime Pack tarball is missing: $Pack (build the win32-x64 pack first; refusing to invent one)"
}
$Pack = (Resolve-Path -LiteralPath $Pack).Path
if ($Pack -notlike "*.tar.gz") {
    Die "Runtime Pack must be the canonical .tar.gz carrier: $Pack"
}
$checksum = "$Pack.sha256"
$signature = "$Pack.sig"
if (-not (Test-Path -LiteralPath $checksum)) { Die "missing checksum beside pack: $checksum" }
if (-not (Test-Path -LiteralPath $signature)) { Die "missing signature beside pack: $signature" }

$versionRaw = (Get-Content -LiteralPath (Join-Path $repoRoot "VERSION") -Raw).Trim()
if ($versionRaw -notmatch '^\d+\.\d+\.\d+') {
    Die "VERSION must be SemVer major.minor.patch: $versionRaw"
}
$versionParts = $versionRaw.Split(".")
while ($versionParts.Count -lt 4) { $versionParts += "0" }
$productVersion = ($versionParts[0..3] -join ".")
$packBasename = [System.IO.Path]::GetFileName($Pack)

$wixZip = Join-Path (Split-Path $cacheRoot) "wix314-binaries.zip"
$wixUrl = "https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip"
if (-not (Test-Path -LiteralPath (Join-Path $cacheRoot "candle.exe"))) {
    New-Item -ItemType Directory -Path (Split-Path $cacheRoot) -Force | Out-Null
    if (-not (Test-Path -LiteralPath $wixZip)) {
        Write-Host "Fetching WiX 3.14 binaries..."
        Invoke-WebRequest -Uri $wixUrl -OutFile $wixZip
    }
    if (Test-Path -LiteralPath $cacheRoot) {
        Remove-Item -LiteralPath $cacheRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $cacheRoot -Force | Out-Null
    Expand-Archive -LiteralPath $wixZip -DestinationPath $cacheRoot -Force
}
$candle = Join-Path $cacheRoot "candle.exe"
$light = Join-Path $cacheRoot "light.exe"
if (-not (Test-Path -LiteralPath $candle)) { Die "WiX candle.exe missing under $cacheRoot" }
if (-not (Test-Path -LiteralPath $light)) { Die "WiX light.exe missing under $cacheRoot" }

if (Test-Path -LiteralPath $stagingRoot) {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force
}
New-Item -ItemType Directory -Path (Join-Path $stagingRoot "scripts") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stagingRoot "pack") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stagingRoot "vibecrafted-core\vibecrafted_core\trust") -Force | Out-Null
Copy-Item (Join-Path $repoRoot "VERSION") (Join-Path $stagingRoot "VERSION")
Copy-Item (Join-Path $repoRoot "scripts\install-runtime-pack.ps1") (Join-Path $stagingRoot "scripts\install-runtime-pack.ps1")
Copy-Item (Join-Path $repoRoot "vibecrafted-core\vibecrafted_core\trust\vibecrafted-signing-v1.pub") `
    (Join-Path $stagingRoot "vibecrafted-core\vibecrafted_core\trust\vibecrafted-signing-v1.pub")
Copy-Item $Pack (Join-Path $stagingRoot "pack\$packBasename")
Copy-Item $checksum (Join-Path $stagingRoot "pack\$packBasename.sha256")
Copy-Item $signature (Join-Path $stagingRoot "pack\$packBasename.sig")

$productTemplate = Join-Path $packagingRoot "Product.wxs"
$bundleTemplate = Join-Path $packagingRoot "Bundle.wxs"
$identityTemplate = Join-Path $packagingRoot "Identity.wxi"
foreach ($path in @($productTemplate, $bundleTemplate, $identityTemplate)) {
    if (-not (Test-Path -LiteralPath $path)) { Die "missing WiX source: $path" }
}

$work = Join-Path $packagingRoot ".cache\work"
if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
New-Item -ItemType Directory -Path $work -Force | Out-Null
New-Item -ItemType Directory -Path $wixObjRoot -Force | Out-Null
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

$identityWork = Join-Path $work "Identity.wxi"
$identityBody = Get-Content -LiteralPath $identityTemplate -Raw
$identityBody = $identityBody -replace 'ProductVersion = "0\.0\.0\.0"', ("ProductVersion = `"{0}`"" -f $productVersion)
Set-Content -LiteralPath $identityWork -Value $identityBody -Encoding utf8

$productWork = Join-Path $work "Product.wxs"
$productBody = Get-Content -LiteralPath $productTemplate -Raw
$productBody = $productBody.Replace("REPLACE_PACK_BASENAME", $packBasename)
Set-Content -LiteralPath $productWork -Value $productBody -Encoding utf8
Copy-Item $bundleTemplate (Join-Path $work "Bundle.wxs")

Push-Location $work
try {
    & $candle -nologo -ext WixUtilExtension -ext WixBalExtension `
        "-dProductVersion=$productVersion" `
        "-bstaging=$stagingRoot" `
        "-bout=$OutDir" `
        Product.wxs Bundle.wxs -out "$wixObjRoot\"
    if ($LASTEXITCODE -ne 0) { Die "candle failed" }

    & $light -nologo -ext WixUtilExtension -ext WixBalExtension `
        "-bstaging=$stagingRoot" `
        "-bout=$OutDir" `
        (Join-Path $wixObjRoot "Product.wixobj") `
        -out (Join-Path $OutDir "Vibecrafted.msi")
    if ($LASTEXITCODE -ne 0) { Die "light MSI failed" }

    & $light -nologo -ext WixUtilExtension -ext WixBalExtension `
        "-bstaging=$stagingRoot" `
        "-bout=$OutDir" `
        (Join-Path $wixObjRoot "Bundle.wixobj") `
        -out (Join-Path $OutDir "Vibecrafted.exe")
    if ($LASTEXITCODE -ne 0) { Die "light Burn EXE failed" }
}
finally {
    Pop-Location
}

Write-Host "MSI: $(Join-Path $OutDir 'Vibecrafted.msi')"
Write-Host "EXE: $(Join-Path $OutDir 'Vibecrafted.exe')"
Write-Host "ProductVersion=$productVersion UpgradeCode=B7E4C2A1-9F3D-4B8E-A6C1-2D5E8F0A1B3C"
Write-Host "Adapter: scripts/install-runtime-pack.ps1 (no install performed by this build)."
