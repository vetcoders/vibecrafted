#Requires -Version 5.1
<#
.SYNOPSIS
    Package a Windows Runtime Pack payload directory into the canonical tar.gz carrier.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PayloadRoot,
    [Parameter(Mandatory = $true)][string]$Output,
    [Parameter(Mandatory = $true)][string]$SourceRevision,
    [Parameter(Mandatory = $true)][string]$TerminalRevision,
    [Parameter(Mandatory = $true)][string]$FrameRevision,
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Platform = "win32-x64",
    [string]$Architecture = "x64"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Die([string]$Message) { Write-Error "Runtime Pack packaging failed: $Message"; exit 1 }

$payload = (Resolve-Path -LiteralPath $PayloadRoot).Path
$required = @(
    "VERSION",
    "bin\python.exe",
    "bin\vibecrafted.cmd",
    "scripts\vetcoders_install.py",
    "scripts\install-runtime-pack.ps1",
    "vibecrafted-core\vibecrafted_core\runtime_pack_contract.py"
)
foreach ($relative in $required) {
    $path = Join-Path $payload $relative
    if (-not (Test-Path -LiteralPath $path)) {
        Die "standalone Runtime Pack is missing $relative"
    }
}

$links = Get-ChildItem -LiteralPath $payload -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }
if ($links) {
    Die "standalone Runtime Pack contains reparse points/symlinks"
}

$python = Join-Path $payload "bin\python.exe"
$env:PYTHONPATH = Join-Path $payload "vibecrafted-core"
$env:PYTHONNOUSERSITE = "1"
& $python -m vibecrafted_core.runtime_pack_contract write `
    --root $payload `
    --carrier-basename ([IO.Path]::GetFileName($Output)) `
    --version $Version `
    --platform $Platform `
    --architecture $Architecture `
    --source-revision $SourceRevision `
    --terminal-revision $TerminalRevision `
    --frame-revision $FrameRevision
if ($LASTEXITCODE -ne 0) { Die "provenance write failed" }

$work = Join-Path ([System.IO.Path]::GetTempPath()) ("vibecrafted-runtime-pack-build-" + [guid]::NewGuid().ToString("N"))
$root = Join-Path $work "VibecraftedRuntime"
New-Item -ItemType Directory -Path $root | Out-Null
Get-ChildItem -LiteralPath $payload -Force | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $root $_.Name) -Recurse -Force
}
if (-not (Test-Path -LiteralPath (Join-Path $root "VERSION"))) {
    Die "payload copy produced an empty staging tree"
}
$outDir = Split-Path -Parent $Output
if ($outDir -and -not (Test-Path -LiteralPath $outDir)) {
    New-Item -ItemType Directory -Path $outDir | Out-Null
}
# Git tar treats `C:` as a remote host and is often first on PATH. Pack-owned
# Python writes the gzip from a relative name so neither Git tar nor tape
# devices can swallow the carrier.
$candidateName = [IO.Path]::GetFileName($Output)
$archivePy = Join-Path $work "write-archive.py"
@'
import os
import tarfile

work = os.environ["VC_PACK_WORK"]
name = os.environ["VC_PACK_NAME"]
os.chdir(work)
with tarfile.open(name, "w:gz", format=tarfile.PAX_FORMAT, dereference=False) as archive:
    archive.add("VibecraftedRuntime", arcname="VibecraftedRuntime", recursive=True)
'@ | Set-Content -LiteralPath $archivePy -Encoding ascii
$env:VC_PACK_WORK = $work
$env:VC_PACK_NAME = $candidateName
& $python $archivePy
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $work $candidateName))) {
    Die "python tarfile archive failed creating $candidateName"
}
Move-Item -LiteralPath (Join-Path $work $candidateName) -Destination $Output -Force
$packed = Get-Item -LiteralPath $Output
if ($packed.Length -lt 1MB) {
    Die "Runtime Pack archive is implausibly small ($($packed.Length) bytes); refusing to publish"
}
Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
$hash = ([BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([System.IO.File]::ReadAllBytes($Output))) -replace '-', '').ToLowerInvariant()
$checksum = "$Output.sha256"
Set-Content -LiteralPath $checksum -Value "$hash  $([IO.Path]::GetFileName($Output))" -Encoding ascii
Write-Output $Output
