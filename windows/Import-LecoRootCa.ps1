<#
.SYNOPSIS
    Trust the LEco DevOps local CA in the Windows certificate store.

.DESCRIPTION
    Traefik runs inside WSL2 and serves *.lh with a certificate signed by mkcert's local
    CA. `mkcert -install` run inside the distro trusts that CA in the *Linux* trust stores
    — but the browser opening https://dashboard.lh is a Windows application reading the
    Windows certificate store, which never saw it. The result is a correctly issued
    certificate that every browser still reports as untrusted.

    This copies the CA that WSL2's mkcert actually used and imports it on the Windows
    side, so both halves agree on the same root.

    Idempotent: an already-imported CA with the same thumbprint is left alone.

.PARAMETER Distro
    WSL distribution holding the mkcert CAROOT. Defaults to the WSL default.

.PARAMETER Scope
    'Machine' (default) imports into LocalMachine\Root and covers every user and service
    account — required if anything other than your interactive browser must trust it.
    'User' imports into CurrentUser\Root and needs no elevation.

.PARAMETER Remove
    Remove a previously imported LEco CA instead of installing one.

.EXAMPLE
    .\Import-LecoRootCa.ps1
    .\Import-LecoRootCa.ps1 -Scope User
    .\Import-LecoRootCa.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$Distro = '',
    [ValidateSet('Machine', 'User')]
    [string]$Scope = 'Machine',
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

function Write-Ok   { param($m) Write-Host "[ OK ] $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Fail { param($m) Write-Host "[FAIL] $m" -ForegroundColor Red }
function Write-Info { param($m) Write-Host "       $m" -ForegroundColor DarkGray }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

$storePath = if ($Scope -eq 'Machine') { 'Cert:\LocalMachine\Root' } else { 'Cert:\CurrentUser\Root' }

if ($Scope -eq 'Machine' -and -not (Test-Admin)) {
    Write-Fail 'Importing into the machine store requires an elevated PowerShell.'
    Write-Info 'Either re-run as Administrator, or use:  -Scope User'
    exit 1
}

Write-Host ''
Write-Host 'LEco DevOps - Windows CA trust' -ForegroundColor Cyan
Write-Host ''

# ------------------------------------------------------------------------------ remove

if ($Remove) {
    # mkcert names its root "mkcert <user>@<host>"; match on that rather than a thumbprint
    # so this works even when the CA was regenerated.
    $found = Get-ChildItem $storePath | Where-Object { $_.Subject -like '*mkcert*' }
    if (-not $found) {
        Write-Warn "No mkcert CA found in $storePath"
        exit 0
    }
    foreach ($cert in $found) {
        Write-Info "Removing $($cert.Subject) [$($cert.Thumbprint)]"
        Remove-Item -Path (Join-Path $storePath $cert.Thumbprint) -Force
    }
    Write-Ok "Removed $($found.Count) certificate(s) from $storePath"
    exit 0
}

# ------------------------------------------------------------------- locate the CA file

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Fail 'WSL is not installed — nothing to import from.'
    exit 1
}

$distroArgs = @()
if ($Distro) { $distroArgs = @('-d', $Distro) }

$caRoot = (& wsl.exe @distroArgs -- sh -lc 'mkcert -CAROOT 2>/dev/null' | Out-String).Trim()
if (-not $caRoot) {
    Write-Fail 'mkcert is not installed inside WSL, or has no CAROOT yet.'
    Write-Info 'Inside the distro, run:  ./certs/generate-certs.sh'
    exit 1
}
Write-Ok "mkcert CAROOT (in WSL): $caRoot"

# Translate the Linux path into something Windows can open.
$winPath = (& wsl.exe @distroArgs -- wslpath -w "$caRoot/rootCA.pem" | Out-String).Trim()
if (-not $winPath -or -not (Test-Path $winPath)) {
    Write-Fail "Could not reach rootCA.pem from Windows (tried: $winPath)"
    Write-Info 'If the distro is stopped, start it and retry.'
    exit 1
}
Write-Ok "CA file: $winPath"

# --------------------------------------------------------------------------- import it

# Import-Certificate wants .cer/.crt; a PEM is the same base64 payload, so copy it to a
# temp .crt rather than asking the user to convert anything.
$temp = Join-Path $env:TEMP 'leco-rootCA.crt'
Copy-Item -Path $winPath -Destination $temp -Force

$incoming = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2 $temp
Write-Info "Subject:    $($incoming.Subject)"
Write-Info "Thumbprint: $($incoming.Thumbprint)"
Write-Info "Expires:    $($incoming.NotAfter)"

$existing = Get-ChildItem $storePath | Where-Object { $_.Thumbprint -eq $incoming.Thumbprint }
if ($existing) {
    Write-Ok "Already trusted in $storePath — nothing to do."
    Remove-Item $temp -Force -ErrorAction SilentlyContinue
    exit 0
}

$stale = Get-ChildItem $storePath | Where-Object {
    $_.Subject -like '*mkcert*' -and $_.Thumbprint -ne $incoming.Thumbprint
}
if ($stale) {
    Write-Warn "$($stale.Count) older mkcert CA(s) present — a regenerated CA leaves the old one behind."
    Write-Info 'Remove them with:  .\Import-LecoRootCa.ps1 -Remove'
}

$null = Import-Certificate -FilePath $temp -CertStoreLocation $storePath
Remove-Item $temp -Force -ErrorAction SilentlyContinue

Write-Ok "Imported into $storePath"
Write-Host ''
Write-Warn 'Restart the browser completely — Chrome, Edge and Firefox cache the trust list.'
Write-Info 'Firefox additionally uses its own NSS store: set'
Write-Info '  security.enterprise_roots.enabled = true  in about:config'
Write-Host ''
