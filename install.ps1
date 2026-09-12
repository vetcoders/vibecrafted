#Requires -Version 5.1
<#
.SYNOPSIS
    Vibecrafted Windows entry point (WSL2-required).

.DESCRIPTION
    Vibecrafted ships a POSIX-shell installer (`install.sh`) and assumes a
    POSIX process model at runtime. There is no native Windows build; on
    Windows the supported path is WSL2. See docs/INSTALL.md for the full
    per-platform channel matrix.

    This script:
      1. Verifies PowerShell >= 5.1.
      2. Detects whether WSL is installed and operational.
      3. If WSL is available: prints the exact one-liner to bootstrap
         Vibecrafted inside the user's default WSL distro.
      4. If WSL is not available: prints the canonical install path
         for WSL2 (winget / Microsoft Store) and exits non-zero so the
         caller knows the install did NOT happen.

    The script NEVER silently succeeds. It is operator-honest: either it
    tells you exactly what to run next, or it tells you what is missing.

.EXAMPLE
    PS> .\install.ps1

    Run from a repository checkout. `https://vibecrafted.io/install.ps1`
    is not served yet; until it is, use the checkout form above or run
    the WSL bootstrap directly:
    PS> wsl bash -c 'curl -fsSL https://vibecrafted.io/install.sh | bash'

.NOTES
    Branding: 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI

    Roadmap:
      - Now: WSL2-required (this script hands off to install.sh inside WSL).
      - Not scheduled: native Windows binaries (PowerShell module + signed
        installer). WSL2 is the supported answer; do not document a native
        Windows build as imminent.
#>

[CmdletBinding()]
param()

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
    Write-Host "  Vibecrafted (Windows entry)" -ForegroundColor Cyan
    Write-Host "  ---------------------------"
    Write-Host "  $Message"
    Write-Host ""
}

function Test-WslAvailable {
    # `wsl --status` is the most reliable presence + health probe across
    # Windows 10 21H1+ and Windows 11. Exit code 0 means WSL is installed
    # and configured. Anything else means missing or broken — same outcome
    # from the caller's perspective (cannot bootstrap from here).
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

function Get-WslDefaultDistro {
    try {
        # `wsl -l -q` lists distro names, default first, in UTF-16LE.
        $raw = & wsl.exe -l -q 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
        $names = ($raw -split "`r?`n") | Where-Object { $_.Trim() -ne '' }
        if ($names.Count -gt 0) { return $names[0].Trim() }
        return $null
    }
    catch {
        return $null
    }
}

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

$productVersion = Get-VibecraftedVersion
$versionLabel = if ($productVersion) { "v$productVersion" } else { "current" }
Write-Banner "Vibecrafted $versionLabel — Windows native install is not shipped. WSL2 is the supported path."

$psVersion = $PSVersionTable.PSVersion
Write-Host "  PowerShell version: $psVersion"

if ($psVersion.Major -lt 5 -or ($psVersion.Major -eq 5 -and $psVersion.Minor -lt 1)) {
    Write-Host ""
    Write-Host "  ERROR: PowerShell 5.1 or newer is required." -ForegroundColor Red
    Write-Host "  Upgrade Windows Management Framework or install PowerShell 7+."
    exit 2
}

if (Test-WslAvailable) {
    $distro = Get-WslDefaultDistro
    $distroLabel = if ($distro) { "$distro (default WSL distro)" } else { "your default WSL distro" }

    Write-Host ""
    Write-Host "  WSL detected." -ForegroundColor Green
    Write-Host ""
    Write-Host "  To install Vibecrafted, run this in PowerShell:"
    Write-Host ""
    Write-Host "    wsl -- bash -c 'curl -fsSL https://vibecrafted.io/install.sh | bash'" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  This will bootstrap Vibecrafted inside $distroLabel via the"
    Write-Host "  POSIX install.sh path. Vibecrafted's CLI will then be available"
    Write-Host "  inside WSL — open a WSL shell and run: vibecrafted help"
    Write-Host ""
    Write-Host "  Native Windows install is not shipped. See docs/INSTALL.md."
    Write-Host "  This script DID NOT install anything."
    Write-Host ""
    # Operator-honest: we did not install. Exit non-zero so any wrapping
    # `iex` / CI step knows to surface this as "next step needed", not done.
    exit 1
}
else {
    Write-Host ""
    Write-Host "  WSL is NOT installed (or not yet operational)." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Install WSL2 first. From an elevated PowerShell:"
    Write-Host ""
    Write-Host "    wsl --install" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  This installs WSL2 with the default Ubuntu distro. Reboot when"
    Write-Host "  prompted, complete the Ubuntu first-run user setup, then"
    Write-Host "  bootstrap Vibecrafted inside WSL:"
    Write-Host ""
    Write-Host "    wsl bash -c 'curl -fsSL https://vibecrafted.io/install.sh | bash'" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Or re-run this checkout entry: .\install.ps1"
    Write-Host "  Docs: https://learn.microsoft.com/windows/wsl/install"
    Write-Host "  Vibecrafted matrix: docs/INSTALL.md (WSL2 is the Windows path)."
    Write-Host ""
    exit 1
}
