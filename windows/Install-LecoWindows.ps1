<#
.SYNOPSIS
    Set up the Windows side of a LEco DevOps install.

.DESCRIPTION
    LEco DevOps itself runs inside WSL2 — this script does only the parts WSL2 structurally
    cannot do for itself, because they belong to Windows:

      1. Preflight   WSL2 version, Docker Desktop integration, port conflicts
      2. Setup       run ./setup.sh inside the distro (the actual install)
      3. DNS         make *.lh resolve for Windows applications
      4. TLS         trust the mkcert CA in the Windows certificate store

    Steps 3 and 4 need Administrator. Step 2 does not, and is skipped with -SkipSetup if
    you would rather run it in the distro yourself.

.PARAMETER Distro
    WSL distribution to use. Defaults to the WSL default distribution.

.PARAMETER RepoPath
    Linux path to the checkout inside WSL. Auto-detected when omitted.

.PARAMETER SkipSetup
    Do the Windows-side steps only; do not run setup.sh in the distro.

.PARAMETER SetupArgs
    Extra arguments passed through to setup.sh (e.g. '--yes --profile ai-full').

.EXAMPLE
    .\Install-LecoWindows.ps1
    .\Install-LecoWindows.ps1 -SetupArgs '--yes'
    .\Install-LecoWindows.ps1 -SkipSetup
#>
[CmdletBinding()]
param(
    [string]$Distro = '',
    [string]$RepoPath = '',
    [switch]$SkipSetup,
    [string]$SetupArgs = '--yes'
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step { param($m) Write-Host ''; Write-Host "== $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "[ OK ] $m" -ForegroundColor Green }
function Write-Warn { param($m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Fail { param($m) Write-Host "[FAIL] $m" -ForegroundColor Red }
function Write-Info { param($m) Write-Host "       $m" -ForegroundColor DarkGray }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host ''
Write-Host 'LEco DevOps - Windows installer' -ForegroundColor Cyan
Write-Host 'The platform runs in WSL2; this handles the Windows-side pieces.' -ForegroundColor DarkGray

$isAdmin = Test-Admin
if (-not $isAdmin) {
    Write-Warn 'Not elevated. DNS and certificate trust will be skipped.'
    Write-Info 'Re-run in an Administrator PowerShell to complete those steps.'
}

# ------------------------------------------------------------------------- 1 · preflight

Write-Step '1/4  Preflight'
$preflight = Join-Path $here 'Test-LecoPreflight.ps1'
$preArgs = @{}
if ($Distro) { $preArgs['Distro'] = $Distro }
& $preflight @preArgs
if ($LASTEXITCODE -ne 0) {
    Write-Fail 'Preflight found blocking issues — resolve them and re-run.'
    exit 1
}

if (-not $Distro) {
    $raw = (& wsl.exe -l -q 2>&1 | Out-String) -replace "`0", ''
    $Distro = ($raw -split "`r?`n" | Where-Object { $_.Trim() -ne '' } | Select-Object -First 1).Trim()
}
Write-Ok "Using distribution: $Distro"

# ----------------------------------------------------------------------- locate the repo

if (-not $RepoPath) {
    $RepoPath = (& wsl.exe -d $Distro -- sh -c 'find ~ -maxdepth 3 -type d -name local-ecosystem 2>/dev/null | head -1' | Out-String).Trim()
}
if (-not $RepoPath) {
    Write-Fail 'Could not find the local-ecosystem checkout inside WSL.'
    Write-Info 'Clone it inside the distro (not under /mnt/c), then pass -RepoPath.'
    Write-Info '  git clone <repo> ~/local-ecosystem'
    exit 1
}
Write-Ok "Checkout: $RepoPath"

if ($RepoPath -like '/mnt/*') {
    Write-Warn 'The checkout is on the Windows drive (/mnt/...).'
    Write-Info 'Every file operation crosses the 9p boundary; this is slow and bind mounts'
    Write-Info 'behave differently. Moving it into the WSL2 filesystem is strongly advised.'
}

# --------------------------------------------------------------------------- 2 · setup

if ($SkipSetup) {
    Write-Step '2/4  Platform setup (skipped)'
    Write-Info "Run it yourself:  wsl -d $Distro -- sh -c 'cd $RepoPath && ./setup.sh'"
} else {
    Write-Step '2/4  Platform setup (inside WSL)'
    Write-Info "cd $RepoPath && ./setup.sh $SetupArgs"
    & wsl.exe -d $Distro -- sh -c "cd '$RepoPath' && ./setup.sh $SetupArgs"
    if ($LASTEXITCODE -ne 0) {
        Write-Fail 'setup.sh failed inside WSL. Fix that before continuing.'
        exit 1
    }
    Write-Ok 'Platform setup completed'
}

# ----------------------------------------------------------------------------- 3 · DNS

Write-Step '3/4  Windows DNS for *.lh'
if ($isAdmin) {
    & (Join-Path $here 'Install-LhDns.ps1') -Distro $Distro
} else {
    Write-Warn 'Skipped (needs Administrator).'
    Write-Info '  .\Install-LhDns.ps1'
}

# ----------------------------------------------------------------------------- 4 · TLS

Write-Step '4/4  Trust the local CA in the Windows store'
if ($isAdmin) {
    & (Join-Path $here 'Import-LecoRootCa.ps1') -Distro $Distro
} else {
    Write-Warn 'Skipped (needs Administrator).'
    Write-Info '  .\Import-LecoRootCa.ps1        (or -Scope User for no elevation)'
}

# --------------------------------------------------------------------------- summary

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
Write-Info 'Dashboard:  http://dashboard.lh   (https once the CA is trusted)'
Write-Info "Operate it: wsl -d $Distro -- sh -c 'cd $RepoPath && ./start.sh --status'"
Write-Info 'Or use the wrapper:  .\leco.ps1 status'
Write-Host ''
