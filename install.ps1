#Requires -Version 5.1
<#
.SYNOPSIS
    Vibecrafted Windows entry point for the native win32-x64 Runtime Pack.

.DESCRIPTION
    Native Windows install is the win32-x64 Runtime Pack. This script:
      1. Preflights every missing prerequisite in one pass (no silent success).
      2. Delegates to scripts/install-runtime-pack.ps1 when a pack is given
         (or VIBECRAFTED_RUNTIME_PACK / a single dist/*.tar.gz is present).
      3. Updates the per-user PATH for %LOCALAPPDATA%\Vibecrafted\bin when
         possible, otherwise prints the exact directory to add.
      4. Prints a human summary (never dumps raw installer JSON as success).

    Layout after a successful install:
      %LOCALAPPDATA%\Vibecrafted           runtime home (active.json, releases)
      %LOCALAPPDATA%\Vibecrafted\bin       public *.cmd launchers
      %LOCALAPPDATA%\Vibecrafted\home       control plane
      %APPDATA%\Vibecrafted                 product config

    WSL2 remains a POSIX alternative, not the native product. Rescue/flock,
    PTY/zsh shells, and voc/vc-o remain POSIX-only and are not claimed here.

.EXAMPLE
    PS> .\install.ps1 -Pack .\build\Vibecrafted_RuntimePack_4.3.1-20260930-abcd1234-win32-x64.tar.gz

.NOTES
    Branding: Vibecrafted. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
    The Windows MSI/EXE carriers are unsigned Authenticode. SmartScreen will
    warn; trust is provenance (.sha256 + .sig), same idea as the portable
    tarball not being Apple-notarized.
#>

[CmdletBinding()]
param(
    [string]$Pack = $env:VIBECRAFTED_RUNTIME_PACK,
    [switch]$Uninstall,
    [switch]$VerifyOnly,
    [switch]$DryRun,
    [string]$ExpectedVersion = "",
    [switch]$SkipPathUpdate
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

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

function Test-WslAvailable {
    $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if (-not $wsl) {
        return $false
    }
    try {
        $null = & wsl.exe --status 2>&1
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function Get-LauncherBinDir {
    $runtimeHome = $env:VIBECRAFTED_RUNTIME_HOME
    if (-not $runtimeHome) {
        $local = $env:LOCALAPPDATA
        if (-not $local) { $local = Join-Path $env:USERPROFILE "AppData\Local" }
        $runtimeHome = Join-Path $local "Vibecrafted"
    }
    return (Join-Path $runtimeHome "bin")
}

function Get-UserPathEntries {
    $raw = [Environment]::GetEnvironmentVariable("Path", "User")
    if ([string]::IsNullOrWhiteSpace($raw)) { return @() }
    return @($raw -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Test-PathContainsDirectory {
    param([string]$Directory)
    $normalized = [System.IO.Path]::GetFullPath($Directory).TrimEnd('\')
    foreach ($entry in Get-UserPathEntries) {
        try {
            $candidate = [System.IO.Path]::GetFullPath($entry).TrimEnd('\')
        }
        catch {
            continue
        }
        if ($candidate.Equals($normalized, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Add-UserPathDirectory {
    param([string]$Directory)
    if (Test-PathContainsDirectory -Directory $Directory) {
        return $true
    }
    $entries = @(Get-UserPathEntries) + @($Directory)
    $joined = ($entries -join ';')
    [Environment]::SetEnvironmentVariable("Path", $joined, "User")
    $env:Path = "$Directory;$env:Path"
    return (Test-PathContainsDirectory -Directory $Directory)
}

function Invoke-Preflight {
    param(
        [string]$PackPath,
        [bool]$NeedPack,
        [bool]$NeedDelegate
    )
    $missing = New-Object System.Collections.Generic.List[string]
    $psVersion = $PSVersionTable.PSVersion
    if ($psVersion.Major -lt 5 -or ($psVersion.Major -eq 5 -and $psVersion.Minor -lt 1)) {
        $missing.Add("PowerShell 5.1+ (found $psVersion)")
    }
    $systemTar = Join-Path $env:SystemRoot "System32\tar.exe"
    if (-not (Test-Path -LiteralPath $systemTar -PathType Leaf)) {
        $missing.Add("Windows System32 tar.exe (required to extract the Runtime Pack)")
    }
    if ($NeedDelegate) {
        $delegate = Join-Path $PSScriptRoot "scripts\install-runtime-pack.ps1"
        if (-not (Test-Path -LiteralPath $delegate -PathType Leaf)) {
            $missing.Add("scripts\install-runtime-pack.ps1 (missing from this checkout)")
        }
    }
    if ($NeedPack) {
        if ([string]::IsNullOrWhiteSpace($PackPath)) {
            $missing.Add("Runtime Pack path (-Pack / VIBECRAFTED_RUNTIME_PACK / dist\*-win32-x64.tar.gz)")
        }
        else {
            if (-not (Test-Path -LiteralPath $PackPath -PathType Leaf)) {
                $missing.Add("Runtime Pack file: $PackPath")
            }
            else {
                if ($PackPath -notlike "*.tar.gz") {
                    $missing.Add("Runtime Pack must be a .tar.gz carrier: $PackPath")
                }
                $checksum = "$PackPath.sha256"
                $signature = "$PackPath.sig"
                if (-not (Test-Path -LiteralPath $checksum -PathType Leaf)) {
                    $missing.Add("sibling checksum: $checksum")
                }
                if (-not (Test-Path -LiteralPath $signature -PathType Leaf)) {
                    $missing.Add("sibling signature: $signature")
                }
            }
        }
        $pub = $env:VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY
        if ([string]::IsNullOrWhiteSpace($pub)) {
            $pub = Join-Path $PSScriptRoot "vibecrafted-core\vibecrafted_core\trust\vibecrafted-signing-v1.pub"
        }
        if (-not (Test-Path -LiteralPath $pub -PathType Leaf)) {
            $missing.Add("trusted Runtime Pack public key: $pub")
        }
    }
    # Customer install must not require a developer toolchain.
    foreach ($devTool in @("cargo", "rustc", "npm", "uv")) {
        if (Get-Command $devTool -ErrorAction SilentlyContinue) {
            # Presence is fine; absence must never be required.
            continue
        }
    }
    return @($missing.ToArray())
}

function Write-HumanInstallSummary {
    param(
        [string]$RawOutput,
        [string]$LauncherBin,
        [bool]$PathUpdated,
        [string]$Mode
    )
    Write-Host ""
    if ($Mode -eq "uninstall") {
        Write-Host "  Uninstall finished." -ForegroundColor Green
        Write-Host "  Launcher directory (should be empty/removed): $LauncherBin"
        if ($RawOutput -match '"status"\s*:\s*"absent"') {
            Write-Host "  Status: already absent (nothing to remove)."
        }
        Write-Host ""
        return
    }
    if ($Mode -eq "verify") {
        Write-Host "  Verify-only: pack provenance checks passed." -ForegroundColor Green
        Write-Host ""
        return
    }
    Write-Host "  Install finished." -ForegroundColor Green
    Write-Host "  Runtime home:  $(Split-Path -Parent $LauncherBin)"
    Write-Host "  Launchers:     $LauncherBin"
    $vibecraftedCmd = Join-Path $LauncherBin "vibecrafted.cmd"
    if (Test-Path -LiteralPath $vibecraftedCmd -PathType Leaf) {
        Write-Host "  Entry point:   $vibecraftedCmd"
    }
    if ($PathUpdated) {
        Write-Host "  PATH updated (User): $LauncherBin"
        Write-Host "  Open a new terminal, then run: vibecrafted doctor"
    }
    else {
        Write-Host "  PATH was not updated automatically." -ForegroundColor Yellow
        Write-Host "  Add this exact directory to your User PATH:" -ForegroundColor Yellow
        Write-Host "    $LauncherBin" -ForegroundColor Cyan
        Write-Host "  Then open a new terminal and run: vibecrafted doctor"
    }
    Write-Host "  First-run orientation: vibecrafted init   (same job as vc-init on macOS)"
    Write-Host "  Unsigned MSI/EXE carriers trigger SmartScreen; pack trust is .sha256 + .sig."
    Write-Host ""
}

$productVersion = Get-VibecraftedVersion
$versionLabel = if ($productVersion) { "v$productVersion" } else { "current" }
$versionForPack = if ($productVersion) { $productVersion } else { "<version>" }
Write-Banner "Vibecrafted $versionLabel - native win32-x64 Runtime Pack."

$psVersion = $PSVersionTable.PSVersion
Write-Host "  PowerShell version: $psVersion"

$delegate = Join-Path $PSScriptRoot "scripts\install-runtime-pack.ps1"
$hasPack = -not [string]::IsNullOrWhiteSpace($Pack)
# Bare -Uninstall must use the installed generation (LOCALAPPDATA projection).
# Auto-binding a leftover dist/*.tar.gz forces checksum+signature verify and
# breaks CI cold-install: install uses the rehearsal pubkey, uninstall would
# fall back to vibecrafted-signing-v1.pub against the same CI-signed pack.
if (-not $hasPack -and -not $Uninstall) {
    $dist = Join-Path $PSScriptRoot "dist"
    if (Test-Path -LiteralPath $dist) {
        $found = @(Get-ChildItem -LiteralPath $dist -Filter "Vibecrafted_RuntimePack_*-win32-x64.tar.gz" -File)
        if ($found.Count -eq 1) { $Pack = $found[0].FullName; $hasPack = $true }
        elseif ($found.Count -gt 1) {
            Write-Host "  ERROR: multiple Runtime Packs in dist/; set -Pack or VIBECRAFTED_RUNTIME_PACK." -ForegroundColor Red
            exit 2
        }
    }
}

$needPack = $hasPack -or (-not $Uninstall -and -not $VerifyOnly)
# Uninstall without -Pack uses the installed generation; verify/install need a pack.
if ($VerifyOnly) { $needPack = $true }
if ($Uninstall -and -not $hasPack) { $needPack = $false }
if ($hasPack -or $Uninstall -or $VerifyOnly) {
    $preflightNeedPack = ($hasPack -or $VerifyOnly)
    $missing = @(Invoke-Preflight -PackPath $Pack -NeedPack:$preflightNeedPack -NeedDelegate:$true)
    if ($missing.Count -gt 0) {
        Write-Host ""
        Write-Host "  Preflight failed - missing prerequisites:" -ForegroundColor Red
        foreach ($item in $missing) {
            Write-Host "    - $item" -ForegroundColor Red
        }
        Write-Host ""
        Write-Host "  Fix every item above, then re-run. No install was performed."
        exit 2
    }

    if (-not (Test-Path -LiteralPath $delegate -PathType Leaf)) {
        Write-Host "  ERROR: missing $delegate" -ForegroundColor Red
        exit 2
    }

    $invoke = @{
        Uninstall = $Uninstall
        VerifyOnly = $VerifyOnly
        DryRun = $DryRun
    }
    if ($hasPack) { $invoke["Pack"] = $Pack }
    if ($ExpectedVersion) { $invoke["ExpectedVersion"] = $ExpectedVersion }

    # The pack installer prints progress on stderr (lease recovery, etc.).
    # Stop mode would turn those NativeCommandError records into a hard abort
    # before we can read $LASTEXITCODE — temporarily Continue for the invoke.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $raw = & $delegate @invoke 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $_.ToString()
            }
            else {
                "$_"
            }
        } | Out-String
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $prevEap
    }
    $launcherBin = Get-LauncherBinDir
    $pathUpdated = $false

    if ($exitCode -ne 0) {
        Write-Host $raw
        Write-Host "  Installer exited with code $exitCode." -ForegroundColor Red
        exit $exitCode
    }

    if ($Uninstall) {
        Write-HumanInstallSummary -RawOutput $raw -LauncherBin $launcherBin -PathUpdated:$false -Mode "uninstall"
        exit 0
    }
    if ($VerifyOnly) {
        # Verify-only still prints contract JSON from the delegate; surface a
        # human line and keep the contract text for CI parsers.
        Write-Host $raw.TrimEnd()
        Write-HumanInstallSummary -RawOutput $raw -LauncherBin $launcherBin -PathUpdated:$false -Mode "verify"
        exit 0
    }

    if (-not $SkipPathUpdate) {
        try {
            $pathUpdated = Add-UserPathDirectory -Directory $launcherBin
        }
        catch {
            $pathUpdated = $false
            Write-Host "  WARN: could not update User PATH: $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    Write-HumanInstallSummary -RawOutput $raw -LauncherBin $launcherBin -PathUpdated:$pathUpdated -Mode "install"
    exit 0
}

$helpMissing = @(Invoke-Preflight -PackPath "" -NeedPack:$false -NeedDelegate:$true)
if ($helpMissing.Count -gt 0) {
    Write-Host ""
    Write-Host "  Preflight failed - missing prerequisites:" -ForegroundColor Red
    foreach ($item in $helpMissing) {
        Write-Host "    - $item" -ForegroundColor Red
    }
    Write-Host ""
}

Write-Host "  Native install uses the win32-x64 Runtime Pack carrier."
Write-Host "  Build one from this checkout, or point -Pack at a release asset:"
Write-Host ""
Write-Host "    powershell -NoProfile -File .\scripts\build-windows-x64-runtime-pack.ps1" -ForegroundColor Cyan
Write-Host "    powershell -NoProfile -File .\install.ps1 -Pack .\build\Vibecrafted_RuntimePack_${versionForPack}-YYYYMMDD-sha8-win32-x64.tar.gz" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Direct installer (checksum + signature before extract):"
Write-Host ""
Write-Host '    powershell -NoProfile -File .\scripts\install-runtime-pack.ps1 -Pack <RuntimePack.tar.gz>' -ForegroundColor Cyan
Write-Host ""
Write-Host "  Runtime home:  %LOCALAPPDATA%\Vibecrafted"
Write-Host "  Launchers:     %LOCALAPPDATA%\Vibecrafted\bin\*.cmd"
Write-Host "  Control plane: %LOCALAPPDATA%\Vibecrafted\home"
Write-Host "  Product config:%APPDATA%\Vibecrafted"
Write-Host ""
Write-Host "  Unsigned Windows MSI/EXE: SmartScreen will warn. Trust is .sha256 + .sig"
Write-Host "  (same idea as the portable tarball not being Apple-notarized)."
Write-Host ""
Write-Host "  For POSIX/source bootstrap, WSL2 is the supported path (install.sh inside WSL)."
if (Test-WslAvailable) {
    Write-Host "  WSL detected - optional handoff:"
    Write-Host "    wsl -- bash -c 'curl -fsSL https://vibecrafted.io/install.sh | bash'" -ForegroundColor Cyan
}
else {
    Write-Host "  WSL not detected. To use the POSIX path: wsl --install, then install.sh inside WSL."
}
Write-Host "  This script DID NOT install anything."
Write-Host ""
exit 1
