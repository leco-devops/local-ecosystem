<#
.SYNOPSIS
    Make *.lh hostnames resolve for Windows applications.

.DESCRIPTION
    The stack runs in WSL2, and the DNS setup done inside the distro
    (ecosystem-stack/scripts/dns-setup.sh) configures the *Linux* resolver. Windows uses
    its own resolver, which cannot see any of it — so a browser on Windows fails to
    resolve dashboard.lh even though `curl dashboard.lh` works fine inside WSL.

    Windows has no /etc/resolver equivalent and its hosts file does not support wildcards,
    so there are exactly two options:

      Hosts   (default) Enumerate the hostnames that exist right now and write them to
              the Windows hosts file as one marked block. Needs no extra software, but is
              not a wildcard: re-run after onboarding an app, or its hostname will not
              resolve.

      Acrylic Install Acrylic DNS Proxy and give it a real `*.lh` wildcard rule. Covers
              future hostnames automatically. Requires installing third-party software and
              repointing the adapter's DNS server, so it is opt-in.

    Both are idempotent and reversible with -Remove.

.PARAMETER Distro
    WSL distribution to read the hostname list from. Defaults to the WSL default.

.PARAMETER Method
    'Hosts' (default) or 'Acrylic'.

.PARAMETER Remove
    Undo whatever this script installed.

.EXAMPLE
    .\Install-LhDns.ps1
    .\Install-LhDns.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$Distro = '',
    [ValidateSet('Hosts', 'Acrylic')]
    [string]$Method = 'Hosts',
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

$Tld        = 'lh'
$MarkBegin  = "# >>> LEco DevOps (*.$Tld) >>>"
$MarkEnd    = "# <<< LEco DevOps (*.$Tld) <<<"
$HostsFile  = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'

function Write-Ok   { param($m) Write-Host "[ OK ] $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Fail { param($m) Write-Host "[FAIL] $m" -ForegroundColor Red }
function Write-Info { param($m) Write-Host "       $m" -ForegroundColor DarkGray }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-Admin)) {
    Write-Fail 'This script edits the Windows hosts file and needs an elevated PowerShell.'
    Write-Info 'Right-click PowerShell > Run as Administrator, then re-run.'
    exit 1
}

Write-Host ''
Write-Host "LEco DevOps - Windows DNS for *.$Tld" -ForegroundColor Cyan
Write-Host ''

# ------------------------------------------------------------------- hosts-block editing

function Remove-HostsBlock {
    if (-not (Test-Path $HostsFile)) { return $false }
    $lines = Get-Content -LiteralPath $HostsFile
    $out = New-Object System.Collections.Generic.List[string]
    $inBlock = $false
    $removed = $false
    foreach ($line in $lines) {
        if ($line.Trim() -eq $MarkBegin) { $inBlock = $true; $removed = $true; continue }
        if ($line.Trim() -eq $MarkEnd)   { $inBlock = $false; continue }
        if (-not $inBlock) { $out.Add($line) }
    }
    if ($removed) {
        Copy-Item $HostsFile "$HostsFile.leco-bak" -Force
        Set-Content -LiteralPath $HostsFile -Value $out -Encoding ASCII
    }
    return $removed
}

function Clear-DnsCacheSafely {
    try { Clear-DnsClientCache } catch { & ipconfig /flushdns | Out-Null }
}

# ------------------------------------------------------------------------------ remove

if ($Remove) {
    if (Remove-HostsBlock) {
        Write-Ok "Removed the LEco block from $HostsFile (backup: $HostsFile.leco-bak)"
    } else {
        Write-Warn 'No LEco block found in the hosts file.'
    }
    Clear-DnsCacheSafely
    Write-Ok 'DNS cache flushed.'
    Write-Host ''
    exit 0
}

# ---------------------------------------------------------------------------- Acrylic

if ($Method -eq 'Acrylic') {
    $acrylicConfig = 'C:\Program Files (x86)\Acrylic DNS Proxy\AcrylicHosts.txt'
    if (-not (Test-Path $acrylicConfig)) {
        Write-Fail 'Acrylic DNS Proxy is not installed.'
        Write-Info 'Install it from https://mayakron.altervista.org/ then re-run with -Method Acrylic.'
        Write-Info 'Or use the default hosts-file method:  .\Install-LhDns.ps1'
        exit 1
    }
    $rule = "127.0.0.1 *.$Tld"
    $current = Get-Content -LiteralPath $acrylicConfig -Raw
    if ($current -match [regex]::Escape($rule)) {
        Write-Ok "Acrylic already has the wildcard rule: $rule"
    } else {
        Add-Content -LiteralPath $acrylicConfig -Value "`r`n$MarkBegin`r`n$rule`r`n$MarkEnd"
        Write-Ok "Added wildcard rule to Acrylic: $rule"
        Restart-Service AcrylicDNSProxy -ErrorAction SilentlyContinue
    }
    Write-Warn 'Point the active adapter''s DNS server at 127.0.0.1 for Acrylic to be consulted.'
    Clear-DnsCacheSafely
    Write-Host ''
    exit 0
}

# ------------------------------------------------------------------ hostname discovery

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Fail 'WSL is not installed — cannot discover which hostnames to add.'
    exit 1
}

$distroArgs = @()
if ($Distro) { $distroArgs = @('-d', $Distro) }

# Ask the Linux side for the hostnames it actually routes, using the same discovery the
# cert generator uses, so the hosts file and the certificate SANs cannot drift apart.
$discover = @'
cd "$(find ~ -maxdepth 3 -type d -name local-ecosystem 2>/dev/null | head -1)" 2>/dev/null || exit 1
{
  printf 'dashboard.lh\ntraefik.lh\nlocalhost.lh\n'
  grep -rhoE 'Host\(`[a-zA-Z0-9][a-zA-Z0-9.*-]*\.lh`\)' traefik/dynamic.yml hosting/traefik/ 2>/dev/null \
    | sed -E 's/Host\(`([^`]*)`\)/\1/'
} | grep -v '^\*' | sort -u
'@

$raw = (& wsl.exe @distroArgs -- sh -c $discover 2>$null | Out-String)
$names = $raw -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' }

if (-not $names) {
    Write-Warn 'Could not discover hostnames from the WSL checkout — using a default set.'
    $names = @('localhost.lh', 'dashboard.lh', 'traefik.lh', 'ollama.lh', 'airllm.lh',
               'ai.lh', 'n8n.lh', 'paperclip.lh', 'mcp.lh')
}

Write-Ok "$($names.Count) hostname(s) to map"
Write-Info ($names -join ' ')

# --------------------------------------------------------------------------- write it

$null = Remove-HostsBlock

$block = New-Object System.Collections.Generic.List[string]
$block.Add($MarkBegin)
foreach ($n in $names) { $block.Add("127.0.0.1`t$n") }
$block.Add($MarkEnd)

Add-Content -LiteralPath $HostsFile -Value $block -Encoding ASCII
Write-Ok "Wrote $($names.Count) entries to $HostsFile"

Clear-DnsCacheSafely
Write-Ok 'DNS cache flushed.'

Write-Host ''
Write-Warn 'This is a fixed list, not a wildcard.'
Write-Info 'Re-run this script after onboarding a new app, or its hostname will not resolve.'
Write-Info 'For automatic coverage of future hostnames, use:  -Method Acrylic'
Write-Host ''
