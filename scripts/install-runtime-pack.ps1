#Requires -Version 5.1
<#
.SYNOPSIS
    Verify and install one Vibecrafted Runtime Pack on native Windows.

.DESCRIPTION
    Same receipted installer as macOS/Linux: checksum + signature before extract,
    pack-owned Python runs scripts/vetcoders_install.py runtime-install.
    Does not require WSL, Homebrew, or a developer toolchain.
#>
[CmdletBinding()]
param(
    [string]$Pack = $env:VIBECRAFTED_RUNTIME_PACK,
    [switch]$Uninstall,
    [switch]$VerifyOnly,
    [switch]$DryRun,
    [string]$ExpectedSourceRevision = "",
    [string]$ExpectedTerminalRevision = "",
    [string]$ExpectedFrameRevision = "",
    [string]$ExpectedVersion = "",
    [string]$ExpectedPlatform = "win32-x64",
    [string]$ExpectedArchitecture = "x64",
    [string]$PublicKey = $env:VIBECRAFTED_RUNTIME_PACK_PUBLIC_KEY
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Die {
    param([string]$Message)
    Write-Error "Runtime Pack install failed: $Message"
    exit 1
}

function Get-RepoRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Get-FileSha256Hex {
    param([string]$Path)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $bytes = $sha.ComputeHash($stream)
            return ([BitConverter]::ToString($bytes) -replace '-', '').ToLowerInvariant()
        }
        finally { $stream.Dispose() }
    }
    finally { $sha.Dispose() }
}

function Get-DerLength {
    param([byte[]]$Data, [ref]$Offset)
    $first = $Data[$Offset.Value]
    $Offset.Value++
    if ($first -lt 0x80) { return [int]$first }
    $count = $first -band 0x7F
    if ($count -lt 1 -or $count -gt 4) { throw "invalid DER length" }
    $length = 0
    for ($i = 0; $i -lt $count; $i++) {
        $length = ($length -shl 8) -bor $Data[$Offset.Value]
        $Offset.Value++
    }
    return [int]$length
}

function Get-DerInteger {
    param([byte[]]$Data, [ref]$Offset)
    if ($Data[$Offset.Value] -ne 0x02) { throw "expected DER INTEGER" }
    $Offset.Value++
    $length = Get-DerLength -Data $Data -Offset $Offset
    $bytes = $Data[$Offset.Value..($Offset.Value + $length - 1)]
    $Offset.Value += $length
    $start = 0
    while ($start -lt ($bytes.Length - 1) -and $bytes[$start] -eq 0) { $start++ }
    if ($start -eq 0) { return $bytes }
    return $bytes[$start..($bytes.Length - 1)]
}

function Import-RsaPublicKeyFromPem {
    param([string]$Pem)
    $b64 = (($Pem -replace '-----BEGIN PUBLIC KEY-----', '') -replace '-----END PUBLIC KEY-----', '') -replace '\s', ''
    $der = [Convert]::FromBase64String($b64)
    $offset = 0
    if ($der[$offset] -ne 0x30) { throw "expected SubjectPublicKeyInfo SEQUENCE" }
    $offset++
    [void](Get-DerLength -Data $der -Offset ([ref]$offset))
    if ($der[$offset] -ne 0x30) { throw "expected algorithm SEQUENCE" }
    $offset++
    $algLen = Get-DerLength -Data $der -Offset ([ref]$offset)
    $offset += $algLen
    if ($der[$offset] -ne 0x03) { throw "expected public key BIT STRING" }
    $offset++
    $bitLen = Get-DerLength -Data $der -Offset ([ref]$offset)
    if ($der[$offset] -ne 0x00) { throw "unexpected BIT STRING padding" }
    $offset++
    $bitEnd = $offset + $bitLen - 1
    if ($der[$offset] -ne 0x30) { throw "expected RSA public key SEQUENCE" }
    $offset++
    [void](Get-DerLength -Data $der -Offset ([ref]$offset))
    $modulus = Get-DerInteger -Data $der -Offset ([ref]$offset)
    $exponent = Get-DerInteger -Data $der -Offset ([ref]$offset)
    if ($offset -gt $bitEnd + 1) { throw "RSA public key DER overflow" }
    $parameters = New-Object System.Security.Cryptography.RSAParameters
    $parameters.Modulus = $modulus
    $parameters.Exponent = $exponent
    $rsa = New-Object System.Security.Cryptography.RSACryptoServiceProvider
    $rsa.PersistKeyInCsp = $false
    $rsa.ImportParameters($parameters)
    return $rsa
}

function Get-OpenSslExe {
    $cmd = Get-Command openssl -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { return [string]$cmd.Source }
    $candidates = @(
        (Join-Path $env:SystemRoot "System32\openssl.exe"),
        (Join-Path ${env:ProgramFiles} "Git\usr\bin\openssl.exe")
    )
    if (${env:ProgramFiles(x86)}) {
        $candidates += (Join-Path ${env:ProgramFiles(x86)} "Git\usr\bin\openssl.exe")
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Test-RuntimePackSignature {
    param(
        [string]$Archive,
        [string]$Signature,
        [string]$KeyPath
    )
    if (-not (Test-Path -LiteralPath $Signature)) {
        Die "Runtime Pack signature is missing: $Signature"
    }
    if (-not (Test-Path -LiteralPath $KeyPath)) {
        Die "trusted Runtime Pack public key is missing: $KeyPath"
    }
    $openssl = Get-OpenSslExe
    if ($openssl) {
        & $openssl dgst -sha256 -verify $KeyPath -signature $Signature $Archive 2>$null
        if ($LASTEXITCODE -eq 0) { return }
        Die "Runtime Pack signature verification failed"
    }
    try {
        $pem = Get-Content -LiteralPath $KeyPath -Raw
        $sig = [System.IO.File]::ReadAllBytes($Signature)
        $hash = [System.Security.Cryptography.SHA256]::Create().ComputeHash(
            [System.IO.File]::ReadAllBytes($Archive)
        )
        if ([System.Security.Cryptography.RSA].GetMethod("ImportFromPem")) {
            $rsa = [System.Security.Cryptography.RSA]::Create()
            $rsa.ImportFromPem($pem)
            $ok = $rsa.VerifyHash(
                $hash,
                $sig,
                [System.Security.Cryptography.HashAlgorithmName]::SHA256,
                [System.Security.Cryptography.RSASignaturePadding]::Pkcs1
            )
        }
        else {
            $rsa = Import-RsaPublicKeyFromPem -Pem $pem
            $oid = [System.Security.Cryptography.CryptoConfig]::MapNameToOID("SHA256")
            $ok = $rsa.VerifyHash($hash, $oid, $sig)
        }
        if (-not $ok) { Die "Runtime Pack signature verification failed" }
        return
    }
    catch {
        Die "cannot verify Runtime Pack signature: $($_.Exception.Message)"
    }
}

function Test-ChecksumFile {
    param([string]$Archive, [string]$ChecksumPath)
    if (-not (Test-Path -LiteralPath $ChecksumPath)) {
        Die "Runtime Pack checksum is missing: $ChecksumPath"
    }
    $expected = ((Get-Content -LiteralPath $ChecksumPath -Raw) -split "\s+")[0].ToLowerInvariant()
    if ($expected -notmatch '^[0-9a-f]{64}$') {
        Die "Runtime Pack checksum file is invalid: $ChecksumPath"
    }
    $actual = Get-FileSha256Hex $Archive
    if ($actual -ne $expected) {
        Die "Runtime Pack checksum mismatch"
    }
}

function Get-WindowsTar {
    $systemTar = Join-Path $env:SystemRoot "System32\tar.exe"
    if (Test-Path -LiteralPath $systemTar) { return $systemTar }
    Die "Windows System32 tar.exe is required to extract the Runtime Pack (Git tar treats C: as a remote host)"
}

function Get-NativeArch {
    if ($env:PROCESSOR_ARCHITECTURE -match 'ARM64') { return "arm64" }
    return "x64"
}

if ($Uninstall -and $VerifyOnly) {
    Die "--VerifyOnly cannot be combined with --Uninstall"
}
if ($DryRun -and -not $Uninstall) {
    Die "--DryRun is only valid with --Uninstall"
}

$repoRoot = Get-RepoRoot
if (-not $ExpectedVersion) {
    $versionFile = Join-Path $repoRoot "VERSION"
    if (Test-Path -LiteralPath $versionFile) {
        $ExpectedVersion = (Get-Content -LiteralPath $versionFile -Raw).Trim()
    }
}
if ($ExpectedArchitecture -eq "") {
    $ExpectedArchitecture = Get-NativeArch
}

if ($Uninstall -and -not $Pack) {
    $runtimeHome = $env:VIBECRAFTED_RUNTIME_HOME
    if (-not $runtimeHome) {
        $local = $env:LOCALAPPDATA
        if (-not $local) { $local = Join-Path $env:USERPROFILE "AppData\Local" }
        $runtimeHome = Join-Path $local "Vibecrafted"
    }
    $receipt = Join-Path $runtimeHome "install-receipt.json"
    if (-not (Test-Path -LiteralPath $receipt)) {
        Write-Output '{"schema":"vibecrafted.runtime-uninstall-result.v1","status":"absent"}'
        exit 0
    }
    $current = Join-Path $runtimeHome "tools\vibecrafted-current"
    if (Test-Path -LiteralPath $current) {
        $generation = (Resolve-Path $current).Path
        $packPython = Join-Path $generation "bin\python.exe"
        $packInstaller = Join-Path $generation "scripts\vetcoders_install.py"
        if (-not (Test-Path -LiteralPath $packPython)) { Die "installed Runtime Pack Python missing: $packPython" }
        if (-not (Test-Path -LiteralPath $packInstaller)) { Die "installed Runtime Pack installer missing: $packInstaller" }
        $arguments = @($packInstaller, "runtime-uninstall")
        if ($DryRun) { $arguments += "--dry-run" }
        & $packPython @arguments
        exit $LASTEXITCODE
    }
    Die "installed Runtime Pack projection is missing; pass -Pack to recover from the receipt"
}

if (-not $Pack) {
    $dist = Join-Path $repoRoot "dist"
    $candidates = @()
    if (Test-Path -LiteralPath $dist) {
        $candidates = @(Get-ChildItem -LiteralPath $dist -Filter "Vibecrafted_RuntimePack_*-$ExpectedPlatform.tar.gz" -File)
    }
    if ($candidates.Count -eq 1) {
        $Pack = $candidates[0].FullName
    }
    elseif ($candidates.Count -gt 1) {
        Die "multiple Runtime Packs in dist; set VIBECRAFTED_RUNTIME_PACK explicitly"
    }
    else {
        Die "no $ExpectedPlatform/$ExpectedArchitecture Runtime Pack found; set VIBECRAFTED_RUNTIME_PACK to the prebuilt release asset"
    }
}

if (-not (Test-Path -LiteralPath $Pack)) {
    Die "cannot resolve Runtime Pack path: $Pack"
}
$Pack = (Resolve-Path -LiteralPath $Pack).Path
if ($Pack -notlike "*.tar.gz") {
    Die "Runtime Pack must be the canonical .tar.gz carrier: $Pack"
}

$hostArch = Get-NativeArch
if ($ExpectedArchitecture -and $ExpectedArchitecture -ne $hostArch) {
    Die "Runtime Pack architecture $ExpectedArchitecture does not match host $hostArch"
}

$checksum = "$Pack.sha256"
$signature = "$Pack.sig"
if (-not $PublicKey) {
    $PublicKey = Join-Path $repoRoot "vibecrafted-core\vibecrafted_core\trust\vibecrafted-signing-v1.pub"
}

Test-ChecksumFile -Archive $Pack -ChecksumPath $checksum
Test-RuntimePackSignature -Archive $Pack -Signature $signature -KeyPath $PublicKey

$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("vibecrafted-runtime-pack-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temporary | Out-Null
try {
    $windowsTar = Get-WindowsTar
    $listing = & $windowsTar -tzf $Pack 2>&1
    if ($LASTEXITCODE -ne 0) { Die "Runtime Pack archive cannot be listed" }
    $archiveRoot = $null
    foreach ($member in $listing) {
        $name = [string]$member
        if ([string]::IsNullOrWhiteSpace($name)) { Die "Runtime Pack archive contains an empty member" }
        if ($name.StartsWith("/") -or $name.Contains("..")) {
            Die "unsafe Runtime Pack archive member: $name"
        }
        $memberRoot = ($name -split "/")[0]
        if (-not $archiveRoot) { $archiveRoot = $memberRoot }
        elseif ($memberRoot -ne $archiveRoot) {
            Die "Runtime Pack archive must contain one root directory"
        }
    }
    if (-not $archiveRoot) { Die "Runtime Pack archive is empty" }
    & $windowsTar -xzf $Pack -C $temporary
    if ($LASTEXITCODE -ne 0) { Die "Runtime Pack archive extraction failed" }
    $payloadRoot = Join-Path $temporary $archiveRoot
    if (-not (Test-Path -LiteralPath $payloadRoot)) {
        Die "runtime payload missing: $payloadRoot"
    }

    $packPython = Join-Path $payloadRoot "bin\python.exe"
    $packInstaller = Join-Path $payloadRoot "scripts\vetcoders_install.py"
    if (-not (Test-Path -LiteralPath $packPython)) { Die "Runtime Pack Python missing: $packPython" }
    if (-not (Test-Path -LiteralPath $packInstaller)) { Die "Runtime Pack installer missing: $packInstaller" }

    $env:PYTHONPATH = (Join-Path $payloadRoot "vibecrafted-core")
    $env:PYTHONNOUSERSITE = "1"
    $contract = @(
        "-m", "vibecrafted_core.runtime_pack_contract", "verify",
        "--root", $payloadRoot,
        "--carrier-basename", [IO.Path]::GetFileName($Pack),
        "--expected-version", $ExpectedVersion,
        "--expected-platform", $ExpectedPlatform,
        "--expected-architecture", $ExpectedArchitecture
    )
    if ($ExpectedSourceRevision) { $contract += @("--expected-source-revision", $ExpectedSourceRevision) }
    if ($ExpectedTerminalRevision) { $contract += @("--expected-terminal-revision", $ExpectedTerminalRevision) }
    if ($ExpectedFrameRevision) { $contract += @("--expected-frame-revision", $ExpectedFrameRevision) }
    $contractOutput = & $packPython @contract
    if ($LASTEXITCODE -ne 0) { Die "Runtime Pack internal provenance verification failed" }
    if ($VerifyOnly) {
        Write-Output $contractOutput
        exit 0
    }
    if ($Uninstall) {
        $arguments = @($packInstaller, "runtime-uninstall")
        if ($DryRun) { $arguments += "--dry-run" }
    }
    else {
        $arguments = @($packInstaller, "runtime-install", "--payload-root", $payloadRoot)
    }
    & $packPython @arguments
    exit $LASTEXITCODE
}
finally {
    if (Test-Path -LiteralPath $temporary) {
        Remove-Item -LiteralPath $temporary -Recurse -Force -ErrorAction SilentlyContinue
    }
}
