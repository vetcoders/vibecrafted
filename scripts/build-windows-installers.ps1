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

function Write-Utf8NoBom([string]$Path, [string]$Content) {
    $encoding = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Assert-NonEmptyFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Die "$Label missing after WiX link: $Path"
    }
    $info = Get-Item -LiteralPath $Path
    if ($info.Length -lt 1) {
        Die "$Label is empty after WiX link: $Path"
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$packagingRoot = Join-Path $repoRoot "packaging\windows"
$cacheRoot = Join-Path $packagingRoot ".cache\wix314"
$stagingRoot = Join-Path $packagingRoot "staging"
$wixObjRoot = Join-Path $packagingRoot ".cache\obj"
$stableUpgradeCode = "B7E4C2A1-9F3D-4B8E-A6C1-2D5E8F0A1B3C"
if (-not $OutDir) { $OutDir = Join-Path $packagingRoot "out" }

if ([string]::IsNullOrWhiteSpace($Pack)) {
    Die "Runtime Pack tarball is missing: (empty -Pack) (build the win32-x64 pack first; refusing to invent one)"
}
if (-not (Test-Path -LiteralPath $Pack -PathType Leaf)) {
    Die "Runtime Pack tarball is missing: $Pack (build the win32-x64 pack first; refusing to invent one)"
}
$Pack = (Resolve-Path -LiteralPath $Pack).Path
if ($Pack -notlike "*.tar.gz") {
    Die "Runtime Pack must be the canonical .tar.gz carrier: $Pack"
}
$checksum = "$Pack.sha256"
$signature = "$Pack.sig"
if (-not (Test-Path -LiteralPath $checksum -PathType Leaf)) { Die "missing checksum beside pack: $checksum" }
if (-not (Test-Path -LiteralPath $signature -PathType Leaf)) { Die "missing signature beside pack: $signature" }

$versionRaw = (Get-Content -LiteralPath (Join-Path $repoRoot "VERSION") -Raw).Trim()
if ($versionRaw -notmatch '^\d+\.\d+\.\d+') {
    Die "VERSION must be SemVer major.minor.patch: $versionRaw"
}
$versionParts = $versionRaw.Split(".")
while ($versionParts.Count -lt 4) { $versionParts += "0" }
$productVersion = ($versionParts[0..3] -join ".")
$packBasename = [System.IO.Path]::GetFileName($Pack)

function Get-BaseSemVer([string]$Value) {
    $trimmed = ($Value -replace '[\r\n]+', '').Trim()
    if ($trimmed -match '^(\d+\.\d+\.\d+)') { return $Matches[1] }
    return $null
}

$repoVersion = Get-BaseSemVer $versionRaw
if (-not $repoVersion) {
    Die "VERSION must be SemVer major.minor.patch: $versionRaw"
}
# Carrier identity: basename SemVer and payload VibecraftedRuntime/VERSION must
# both agree with repo VERSION. Never stamp ProductVersion onto a skew carrier.
if ($packBasename -notmatch '^Vibecrafted_RuntimePack_(\d+\.\d+\.\d+(?:[+-][0-9A-Za-z.-]+)?)-') {
    Die "Runtime Pack basename must carry SemVer after Vibecrafted_RuntimePack_: $packBasename"
}
$packNameVersion = Get-BaseSemVer $Matches[1]
if (-not $packNameVersion) {
    Die "Runtime Pack basename SemVer unreadable: $packBasename"
}
$tar = Get-Command tar -ErrorAction SilentlyContinue
if (-not $tar) {
    Die "tar is required to read Runtime Pack payload VERSION (VibecraftedRuntime/VERSION)"
}
$payloadVersionRaw = & $tar.Source -xOf $Pack "VibecraftedRuntime/VERSION" 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace([string]$payloadVersionRaw)) {
    Die "Runtime Pack payload VERSION missing or unreadable: VibecraftedRuntime/VERSION in $Pack"
}
$payloadVersion = Get-BaseSemVer ([string]$payloadVersionRaw)
if (-not $payloadVersion) {
    Die "Runtime Pack payload VERSION must be SemVer major.minor.patch: $payloadVersionRaw"
}
if ($packNameVersion -ne $payloadVersion) {
    Die "Runtime Pack basename version ($packNameVersion) disagrees with payload VERSION ($payloadVersion)"
}
if ($payloadVersion -ne $repoVersion) {
    Die "Runtime Pack VERSION ($payloadVersion) disagrees with repo VERSION ($repoVersion); refusing to stamp ProductVersion=$productVersion onto a $payloadVersion carrier"
}

$identityTemplate = Join-Path $packagingRoot "Identity.wxi"
$identityText = Get-Content -LiteralPath $identityTemplate -Raw
if ($identityText -notmatch [regex]::Escape($stableUpgradeCode)) {
    Die "Identity.wxi UpgradeCode drifted from stable lineage $stableUpgradeCode"
}

$wixZip = Join-Path (Split-Path $cacheRoot) "wix314-binaries.zip"
$wixUrl = "https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/wix314-binaries.zip"
if (-not (Test-Path -LiteralPath (Join-Path $cacheRoot "candle.exe"))) {
    New-Item -ItemType Directory -Path (Split-Path $cacheRoot) -Force | Out-Null
    if (-not (Test-Path -LiteralPath $wixZip -PathType Leaf)) {
        Write-Host "Fetching WiX 3.14 binaries..."
        try {
            Invoke-WebRequest -Uri $wixUrl -OutFile $wixZip
        }
        catch {
            Die "WiX 3.14 download failed from $wixUrl : $($_.Exception.Message)"
        }
    }
    $zipInfo = Get-Item -LiteralPath $wixZip -ErrorAction SilentlyContinue
    if (-not $zipInfo -or $zipInfo.Length -lt 1) {
        Die "WiX 3.14 zip missing or empty: $wixZip"
    }
    if (Test-Path -LiteralPath $cacheRoot) {
        Remove-Item -LiteralPath $cacheRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $cacheRoot -Force | Out-Null
    try {
        Expand-Archive -LiteralPath $wixZip -DestinationPath $cacheRoot -Force
    }
    catch {
        Die "WiX 3.14 zip extract failed: $($_.Exception.Message)"
    }
}
$candle = Join-Path $cacheRoot "candle.exe"
$light = Join-Path $cacheRoot "light.exe"
if (-not (Test-Path -LiteralPath $candle -PathType Leaf)) { Die "WiX candle.exe missing under $cacheRoot" }
if (-not (Test-Path -LiteralPath $light -PathType Leaf)) { Die "WiX light.exe missing under $cacheRoot" }

$requiredStaging = @(
    (Join-Path $repoRoot "VERSION"),
    (Join-Path $repoRoot "scripts\install-runtime-pack.ps1"),
    (Join-Path $repoRoot "vibecrafted-core\vibecrafted_core\trust\vibecrafted-signing-v1.pub")
)
foreach ($required in $requiredStaging) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        Die "required installer payload missing: $required"
    }
}

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
$licenseRtf = Join-Path $packagingRoot "License.rtf"
foreach ($path in @($productTemplate, $bundleTemplate, $identityTemplate, $licenseRtf)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { Die "missing WiX source: $path" }
}
$licenseText = Get-Content -LiteralPath $licenseRtf -Raw
if ($licenseText -notmatch "Business Source License" -or $licenseText -notmatch "Individual developers and small teams") {
    Die "License.rtf must carry the repo BUSL-1.1 LICENSE text (refusing a placeholder)"
}
$repoLicense = Get-Content -LiteralPath (Join-Path $repoRoot "LICENSE") -Raw
if ($repoLicense -notmatch "Licensor:\s+LibraxisAI") {
    Die "repo LICENSE Licensor must remain LibraxisAI"
}

$work = Join-Path $packagingRoot ".cache\work"
if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
New-Item -ItemType Directory -Path $work -Force | Out-Null
New-Item -ItemType Directory -Path $wixObjRoot -Force | Out-Null
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

# Stamp ProductVersion into Identity only. Do not also redefine it on the candle command line.
$identityWork = Join-Path $work "Identity.wxi"
$identityBody = Get-Content -LiteralPath $identityTemplate -Raw
if ($identityBody -notmatch 'ProductVersion = "0\.0\.0\.0"') {
    Die "Identity.wxi must keep ProductVersion placeholder 0.0.0.0 for the builder stamp"
}
$identityBody = $identityBody -replace 'ProductVersion = "0\.0\.0\.0"', ("ProductVersion = `"{0}`"" -f $productVersion)
Write-Utf8NoBom -Path $identityWork -Content $identityBody

$productWork = Join-Path $work "Product.wxs"
$productBody = Get-Content -LiteralPath $productTemplate -Raw
if ($productBody -notmatch "REPLACE_PACK_BASENAME") {
    Die "Product.wxs missing REPLACE_PACK_BASENAME placeholders"
}
if ($productBody -notmatch 'InstallScope="perUser"') {
    Die "Product.wxs must stay InstallScope=perUser for portable installs"
}
if ($productBody -notmatch "LocalAppDataFolder") {
    Die "Product.wxs must install under LocalAppDataFolder (per-user portable)"
}
if ($productBody -notmatch "WixUILicenseRtf" -or $productBody -notmatch "WixUI_Minimal") {
    Die "Product.wxs must wire WixUI_Minimal + WixUILicenseRtf for the BUSL license dialog"
}
if ($productBody -notmatch "LaunchVcTerminal") {
    Die "Product.wxs must launch vc-terminal after install"
}
$productBody = $productBody.Replace("REPLACE_PACK_BASENAME", $packBasename)
Write-Utf8NoBom -Path $productWork -Content $productBody
Copy-Item $bundleTemplate (Join-Path $work "Bundle.wxs")
Copy-Item $licenseRtf (Join-Path $work "License.rtf")

$msiOut = Join-Path $OutDir "Vibecrafted.msi"
$exeOut = Join-Path $OutDir "Vibecrafted.exe"
foreach ($stale in @($msiOut, $exeOut)) {
    if (Test-Path -LiteralPath $stale) {
        Remove-Item -LiteralPath $stale -Force
    }
}

Push-Location $work
try {
    # Bindpaths are light-only: use spaced binder path args (name=path).
    # WixUIExtension supplies the MSI license dialog; BalExtension supplies Burn RtfLicense.
    & $candle -nologo -ext WixUtilExtension -ext WixBalExtension -ext WixUIExtension `
        Product.wxs Bundle.wxs -out "$wixObjRoot\"
    if ($LASTEXITCODE -ne 0) { Die "candle failed (exit $LASTEXITCODE)" }

    & $light -nologo -ext WixUtilExtension -ext WixBalExtension -ext WixUIExtension `
        -b "staging=$stagingRoot" `
        -b "out=$OutDir" `
        (Join-Path $wixObjRoot "Product.wixobj") `
        -out $msiOut
    if ($LASTEXITCODE -ne 0) { Die "light MSI failed (exit $LASTEXITCODE)" }
    Assert-NonEmptyFile -Path $msiOut -Label "MSI"

    & $light -nologo -ext WixUtilExtension -ext WixBalExtension -ext WixUIExtension `
        -b "staging=$stagingRoot" `
        -b "out=$OutDir" `
        (Join-Path $wixObjRoot "Bundle.wixobj") `
        -out $exeOut
    if ($LASTEXITCODE -ne 0) { Die "light Burn EXE failed (exit $LASTEXITCODE)" }
    Assert-NonEmptyFile -Path $exeOut -Label "Burn EXE"
}
finally {
    Pop-Location
}

Write-Host "MSI: $msiOut"
Write-Host "EXE: $exeOut"
Write-Host "ProductVersion=$productVersion UpgradeCode=$stableUpgradeCode"
Write-Host "Adapter: scripts/install-runtime-pack.ps1 (no install performed by this build)."
