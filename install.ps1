#Requires -Version 5.1
<#
.SYNOPSIS
    Vibecrafted Windows entry point for the native win32-x64 Runtime Pack.

.DESCRIPTION
    Native Windows install is the win32-x64 Runtime Pack. This script:
      1. Requires PowerShell 5.1+.
      2. Delegates to scripts/install-runtime-pack.ps1 when a pack is given
         (or VIBECRAFTED_RUNTIME_PACK / a single dist/*.tar.gz is present).
      3. Otherwise prints the exact native install command and exits
         non-zero so wrapping iex/CI cannot treat "printed help" as done.

    Layout after a successful install:
      %LOCALAPPDATA%\Vibecrafted           runtime home (active.json, releases)
      %LOCALAPPDATA%\Vibecrafted\bin       public *.cmd launchers
      %LOCALAPPDATA%\Vibecrafted\home       control plane
      %APPDATA%\Vibecrafted                 product config

    WSL2 remains a POSIX alternative, not the native product. Rescue/flock
    recovery is POSIX-only and is not claimed to work here.

.EXAMPLE
    PS> .\install.ps1 -Pack .\dist\Vibecrafted_RuntimePack_4.3.1-win32-x64.tar.gz

.NOTES
    Branding: 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
#>

[CmdletBinding()]
param(
    [string]$Pack = $env:VIBECRAFTED_RUNTIME_PACK,
    [switch]$Uninstall,
    [switch]$VerifyOnly,
    [switch]$DryRun,
    [string]$ExpectedVersion = ""
)

$ErrorActionPreference = 'Stop'

function Get-VibecraftedVersion {
    $versionFile = Join-Path $PSScriptRoot "VERSION"
    if (Test-Path -LiteralPath $versionFile) {
        return (Get-Content -LiteralPath $versionFile -Raw).Trim()
    }
    return $null
}

function Write-Banner {
    param([string]$Message)
    Write-Host ""
    Write-Host "  Vibecrafted (Windows native Runtime Pack)" -ForegroundColor Cyan
    Write-Host "  ------------------------------------------"
    Write-Host "  $Message"
    Write-Host ""
}

$productVersion = Get-VibecraftedVersion
$versionLabel = if ($productVersion) { "v$productVersion" } else { "current" }
$versionForPack = if ($productVersion) { $productVersion } else { "<version>" }
Write-Banner "Vibecrafted $versionLabel — native win32-x64 Runtime Pack."

$psVersion = $PSVersionTable.PSVersion
Write-Host "  PowerShell version: $psVersion"

if ($psVersion.Major -lt 5 -or ($psVersion.Major -eq 5 -and $psVersion.Minor -lt 1)) {
    Write-Host ""
    Write-Host "  ERROR: PowerShell 5.1 or newer is required." -ForegroundColor Red
    Write-Host "  Upgrade Windows Management Framework or install PowerShell 7+."
    exit 2
}

$delegate = Join-Path $PSScriptRoot "scripts\install-runtime-pack.ps1"
if (-not (Test-Path -LiteralPath $delegate)) {
    Write-Host "  ERROR: missing $delegate" -ForegroundColor Red
    exit 2
}

$hasPack = -not [string]::IsNullOrWhiteSpace($Pack)
if (-not $hasPack) {
    $dist = Join-Path $PSScriptRoot "dist"
    if (Test-Path -LiteralPath $dist) {
        $found = @(Get-ChildItem -LiteralPath $dist -Filter "Vibecrafted_RuntimePack_*-win32-x64.tar.gz" -File)
        if ($found.Count -eq 1) { $Pack = $found[0].FullName; $hasPack = $true }
    }
}

if ($hasPack -or $Uninstall -or $VerifyOnly) {
    $invoke = @{
        Uninstall = $Uninstall
        VerifyOnly = $VerifyOnly
        DryRun = $DryRun
    }
    if ($hasPack) { $invoke["Pack"] = $Pack }
    if ($ExpectedVersion) { $invoke["ExpectedVersion"] = $ExpectedVersion }
    & $delegate @invoke
    exit $LASTEXITCODE
}

Write-Host "  Native install uses the win32-x64 Runtime Pack carrier."
Write-Host "  Build one from this checkout, or point -Pack at a release asset:"
Write-Host ""
Write-Host "    powershell -NoProfile -File .\scripts\build-windows-x64-runtime-pack.ps1" -ForegroundColor Cyan
Write-Host "    powershell -NoProfile -File .\install.ps1 -Pack .\build\Vibecrafted_RuntimePack_${versionForPack}-win32-x64.tar.gz" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Direct installer (checksum + signature before extract):"
Write-Host ""
Write-Host "    powershell -NoProfile -File .\scripts\install-runtime-pack.ps1 -Pack <RuntimePack.tar.gz>" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Runtime home:  %LOCALAPPDATA%\Vibecrafted"
Write-Host "  Launchers:     %LOCALAPPDATA%\Vibecrafted\bin\*.cmd"
Write-Host "  Control plane: %LOCALAPPDATA%\Vibecrafted\home"
Write-Host "  Product config:%APPDATA%\Vibecrafted"
Write-Host ""
Write-Host "  POSIX alternative (not native): install WSL2 and use install.sh inside it."
Write-Host "  This script DID NOT install anything."
Write-Host ""
exit 1
