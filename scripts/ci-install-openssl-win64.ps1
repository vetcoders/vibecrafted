#Requires -Version 5.1
<#
.SYNOPSIS
    Deterministic OpenSSL Win64 install for Windows CI (vc-frame / OPENSSL_DIR).

.DESCRIPTION
    Downloads a pinned FireDaemon OpenSSL x64 EXE with curl.exe (retries +
    per-attempt max-time), verifies size + SHA-256, then silent-installs to a
    fixed directory. Avoids package managers that stall for hours on GHA.
    FireDaemon ships a flat lib\ (libssl.lib) plus bin\libssl-3-x64.dll; Shining
    Light-style lib\VC\x64\MD\ remains accepted when present.
#>
[CmdletBinding()]
param(
    [string]$InstallDir = "C:\Program Files\OpenSSL-Win64"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# FireDaemon OpenSSL 3.5.2 x64 (Win64 OpenSSL 3 layout the pack links:
# include\, lib\ or lib\VC\x64\MD\, bin\libssl-3-x64.dll / libcrypto-3-x64.dll).
# URL + SHA-256 match the FireDaemon.OpenSSL 3.5.2 installer published for Win64.
$Url = "https://download.firedaemon.com/FireDaemon-OpenSSL/FireDaemon-OpenSSL-x64-3.5.2.exe"
$ExpectedSha256 = "69AD22A9CB82D5CF93BA740CDFF0996BE794627BA88C1C478666F5375003DE0A"
$ExpectedBytes = 14949016
$CurlMaxTimeSec = 120
$MaxAttempts = 3
$RetryDelaySec = 5

function Die([string]$Message) {
    Write-Error "ci-install-openssl-win64: $Message"
    exit 1
}

function Get-OpenSslLibDir([string]$Root) {
    $libMd = Join-Path $Root "lib\VC\x64\MD"
    if (Test-Path -LiteralPath $libMd -PathType Container) {
        return $libMd
    }
    $libFlat = Join-Path $Root "lib"
    $sslLib = Join-Path $libFlat "libssl.lib"
    $cryptoLib = Join-Path $libFlat "libcrypto.lib"
    if (
        (Test-Path -LiteralPath $sslLib -PathType Leaf) -and
        (Test-Path -LiteralPath $cryptoLib -PathType Leaf)
    ) {
        return $libFlat
    }
    return $null
}

function Test-OpenSslLayout([string]$Root) {
    if (-not $Root) { return $false }
    $header = Join-Path $Root "include\openssl\ssl.h"
    $sslDll = Join-Path $Root "bin\libssl-3-x64.dll"
    $cryptoDll = Join-Path $Root "bin\libcrypto-3-x64.dll"
    $libDir = Get-OpenSslLibDir $Root
    return (
        (Test-Path -LiteralPath $header -PathType Leaf) -and
        ($null -ne $libDir) -and
        (Test-Path -LiteralPath $sslDll -PathType Leaf) -and
        (Test-Path -LiteralPath $cryptoDll -PathType Leaf)
    )
}

function Resolve-OpenSslRoot([string]$Preferred) {
    $candidates = @(
        $Preferred,
        "C:\Program Files\OpenSSL-Win64",
        "C:\Program Files\FireDaemon OpenSSL 3.5",
        "C:\Program Files\FireDaemon OpenSSL 3",
        "C:\Program Files\FireDaemon OpenSSL",
        (Join-Path ${env:ProgramFiles} "OpenSSL-Win64"),
        (Join-Path ${env:ProgramFiles} "FireDaemon OpenSSL 3.5"),
        (Join-Path ${env:ProgramFiles} "FireDaemon OpenSSL 3")
    ) | Where-Object { $_ } | Select-Object -Unique
    foreach ($candidate in $candidates) {
        if (Test-OpenSslLayout $candidate) {
            return $candidate
        }
    }
    return $null
}

$resolved = Resolve-OpenSslRoot $InstallDir
if ($resolved) {
    $InstallDir = $resolved
    Write-Host "OpenSSL already present at $InstallDir"
}
else {
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) { Die "curl.exe is required (do not use Invoke-WebRequest for this download)" }

    $work = Join-Path ([System.IO.Path]::GetTempPath()) ("vc-openssl-ci-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    $installer = Join-Path $work "FireDaemon-OpenSSL-x64-3.5.2.exe"
    $installLog = Join-Path $work "fdopenssl.log"
    $downloaded = $false

    try {
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
            Write-Host "OpenSSL download attempt $attempt/$MaxAttempts (max-time ${CurlMaxTimeSec}s): $Url"
            if (Test-Path -LiteralPath $installer) {
                Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
            }
            & curl.exe `
                --fail `
                --location `
                --silent `
                --show-error `
                --retry 2 `
                --retry-delay $RetryDelaySec `
                --retry-all-errors `
                --connect-timeout 30 `
                --max-time $CurlMaxTimeSec `
                --output $installer `
                $Url
            $curlExit = $LASTEXITCODE
            if ($curlExit -ne 0) {
                Write-Host "curl exit $curlExit on attempt $attempt"
                Start-Sleep -Seconds ($RetryDelaySec * $attempt)
                continue
            }
            if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
                Write-Host "installer missing after curl on attempt $attempt"
                Start-Sleep -Seconds ($RetryDelaySec * $attempt)
                continue
            }
            $size = (Get-Item -LiteralPath $installer).Length
            if ($size -ne $ExpectedBytes) {
                Write-Host "size mismatch on attempt ${attempt}: got $size expected $ExpectedBytes"
                Start-Sleep -Seconds ($RetryDelaySec * $attempt)
                continue
            }
            $actual = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToUpperInvariant()
            if ($actual -ne $ExpectedSha256.ToUpperInvariant()) {
                Write-Host "SHA-256 mismatch on attempt ${attempt}: got $actual expected $ExpectedSha256"
                Start-Sleep -Seconds ($RetryDelaySec * $attempt)
                continue
            }
            $downloaded = $true
            break
        }

        if (-not $downloaded) {
            Die "OpenSSL download failed after $MaxAttempts attempts (url=$Url max-time=${CurlMaxTimeSec}s)"
        }

        Write-Host "Installing OpenSSL silently to $InstallDir"
        # FireDaemon Advanced Installer bootstrapper. Pass one ArgumentList string so
        # APPDIR with spaces is not mangled by Start-Process array quoting.
        $argString = "/exenoui /exelog `"$installLog`" /qn /norestart REBOOT=ReallySuppress ADJUSTSYSTEMPATHENV=yes APPDIR=`"$InstallDir`""
        $proc = Start-Process -FilePath $installer -ArgumentList $argString -Wait -PassThru
        if ($proc.ExitCode -ne 0) {
            if (Test-Path -LiteralPath $installLog -PathType Leaf) {
                Write-Host "--- FireDaemon OpenSSL install log ---"
                Get-Content -LiteralPath $installLog -ErrorAction SilentlyContinue | Select-Object -Last 80
            }
            Die "OpenSSL silent install failed with exit $($proc.ExitCode)"
        }
    }
    finally {
        # Keep the log path out of the wiped work dir copy when diagnosing, but the
        # work tree is ephemeral and must not leak the installer EXE.
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }

    $resolved = Resolve-OpenSslRoot $InstallDir
    if (-not $resolved) {
        Die "OpenSSL layout incomplete after install under $InstallDir (need include\, lib\ or lib\VC\x64\MD\, bin\libssl-3-x64.dll)"
    }
    $InstallDir = $resolved
    Write-Host "OpenSSL resolved at $InstallDir"
}

$libDir = Get-OpenSslLibDir $InstallDir
if (-not $libDir) {
    Die "OpenSSL lib dir missing under $InstallDir (need lib\VC\x64\MD\ or lib\libssl.lib)"
}
$inc = Join-Path $InstallDir "include"

Write-Host "OPENSSL_DIR=$InstallDir"
Write-Host "OPENSSL_LIB_DIR=$libDir"
Write-Host "OPENSSL_INCLUDE_DIR=$inc"
if ($env:GITHUB_ENV) {
    "OPENSSL_DIR=$InstallDir" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
    "OPENSSL_LIB_DIR=$libDir" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
    "OPENSSL_INCLUDE_DIR=$inc" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
}

exit 0
