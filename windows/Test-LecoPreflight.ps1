<#
.SYNOPSIS
    Check that this Windows host can run LEco DevOps under WSL2.

.DESCRIPTION
    LEco DevOps runs inside WSL2: all 29 orchestration scripts are bash, and the stack is
    Linux containers. Nothing here starts anything — it reports the Windows-side conditions
    that make the WSL2 install work or silently fail:

      * WSL2 present, and the target distro is version 2 (not 1)
      * Docker Desktop running with WSL integration enabled for that distro
      * Ports 80/443 free — IIS and the "World Wide Web Publishing Service" commonly hold
        them, and Hyper-V reserves dynamic ranges that can swallow 8080/5432 without
        anything appearing to listen on them
      * The repo checked out inside the WSL2 filesystem rather than /mnt/c

    Exit code 0 when everything needed is present, 1 when something blocking was found.

.PARAMETER Distro
    WSL distribution to check. Defaults to the WSL default distribution.

.EXAMPLE
    .\Test-LecoPreflight.ps1
    .\Test-LecoPreflight.ps1 -Distro Ubuntu-22.04
#>
[CmdletBinding()]
param(
    [string]$Distro = ''
)

$ErrorActionPreference = 'Stop'
$script:Blocking = 0

function Write-Ok    { param($m) Write-Host "[ OK ] $m" -ForegroundColor Green }
function Write-Warn  { param($m) Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Fail  { param($m) Write-Host "[FAIL] $m" -ForegroundColor Red; $script:Blocking++ }
function Write-Info  { param($m) Write-Host "       $m" -ForegroundColor DarkGray }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host ''
Write-Host 'LEco DevOps - Windows preflight' -ForegroundColor Cyan
Write-Host ''

# --------------------------------------------------------------------------- WSL2

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Fail 'WSL is not installed.'
    Write-Info 'Install it (elevated), then reboot:  wsl --install'
    Write-Host ''
    exit 1
}

# `wsl -l -v` emits UTF-16 with NUL bytes; strip them before parsing.
$rawList = (& wsl.exe -l -v 2>&1 | Out-String) -replace "`0", ''
$lines = $rawList -split "`r?`n" | Where-Object { $_.Trim() -ne '' } | Select-Object -Skip 1

$distros = foreach ($line in $lines) {
    # Format: "* Ubuntu    Running    2"
    $isDefault = $line.TrimStart().StartsWith('*')
    $parts = ($line -replace '^\s*\*?\s*', '') -split '\s+' | Where-Object { $_ -ne '' }
    if ($parts.Count -ge 3) {
        [pscustomobject]@{
            Name      = $parts[0]
            State     = $parts[1]
            Version   = $parts[2]
            IsDefault = $isDefault
        }
    }
}

if (-not $distros) {
    Write-Fail 'WSL is installed but no distribution exists.'
    Write-Info 'Install one:  wsl --install -d Ubuntu'
    Write-Host ''
    exit 1
}

if (-not $Distro) {
    $default = $distros | Where-Object { $_.IsDefault } | Select-Object -First 1
    if (-not $default) { $default = $distros | Select-Object -First 1 }
    $Distro = $default.Name
}

$target = $distros | Where-Object { $_.Name -eq $Distro } | Select-Object -First 1
if (-not $target) {
    Write-Fail "Distribution '$Distro' not found. Available: $(($distros.Name) -join ', ')"
    Write-Host ''
    exit 1
}

Write-Ok "WSL distribution: $($target.Name) (state: $($target.State))"

if ($target.Version -ne '2') {
    Write-Fail "'$Distro' is WSL version $($target.Version); LEco DevOps needs version 2."
    Write-Info "Convert it:  wsl --set-version $Distro 2"
} else {
    Write-Ok 'WSL version 2'
}

# --------------------------------------------------------------------- Docker Desktop

$dockerOk = $false
try {
    $null = & wsl.exe -d $Distro -- docker info 2>$null
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
} catch { $dockerOk = $false }

if ($dockerOk) {
    Write-Ok "Docker reachable from inside '$Distro'"
} else {
    Write-Fail "Docker is not reachable from inside '$Distro'."
    Write-Info 'Start Docker Desktop, then enable integration for this distro:'
    Write-Info '  Settings > Resources > WSL Integration > enable for ' + $Distro
}

# ------------------------------------------------------------------------- port checks

function Test-PortHeld {
    param([int]$Port)
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($conn) {
            $procIds = $conn.OwningProcess | Sort-Object -Unique
            $names = foreach ($procId in $procIds) {
                (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
            }
            return ($names | Where-Object { $_ } | Sort-Object -Unique) -join ', '
        }
    } catch { }
    return $null
}

foreach ($port in 80, 443, 8090) {
    $holder = Test-PortHeld -Port $port
    if ($holder) {
        Write-Fail "Port $port is already in use by: $holder"
        if ($port -in 80, 443) {
            Write-Info 'If that is IIS/W3SVC:  Stop-Service W3SVC; Set-Service W3SVC -StartupType Disabled'
        }
    } else {
        Write-Ok "Port $port is free"
    }
}

$w3svc = Get-Service -Name W3SVC -ErrorAction SilentlyContinue
if ($w3svc -and $w3svc.Status -eq 'Running') {
    Write-Warn 'World Wide Web Publishing Service (IIS) is running and will contend for 80/443.'
}

# Hyper-V / WinNAT reserve dynamic TCP ranges. Nothing listens on them, but a bind fails
# anyway - which reads as an inexplicable "port already in use" with no process to blame.
try {
    $excluded = (& netsh int ipv4 show excludedportrange protocol=tcp 2>&1 | Out-String)
    $ranges = [regex]::Matches($excluded, '(\d+)\s+(\d+)')
    $hit = $false
    foreach ($m in $ranges) {
        $start = [int]$m.Groups[1].Value
        $end   = [int]$m.Groups[2].Value
        foreach ($p in 80, 443, 8080, 8090, 8099, 5432) {
            if ($p -ge $start -and $p -le $end) {
                Write-Fail "Port $p falls inside a reserved range ($start-$end) and cannot be bound."
                $hit = $true
            }
        }
    }
    if (-not $hit) { Write-Ok 'No required port falls in a Windows reserved range' }
} catch {
    Write-Warn 'Could not read Windows reserved port ranges.'
}

# ------------------------------------------------------------------- checkout location

try {
    $repoPath = (& wsl.exe -d $Distro -- sh -c 'cd "$HOME" 2>/dev/null; pwd' 2>$null | Out-String).Trim()
    if ($repoPath) { Write-Ok "WSL home reachable: $repoPath" }
} catch { }

Write-Warn 'Check where the repo is cloned: a checkout under /mnt/c crosses the 9p'
Write-Info 'boundary on every file operation and makes bind mounts behave differently.'
Write-Info 'Clone inside the WSL2 filesystem (e.g. ~/local-ecosystem) instead.'

# ------------------------------------------------------------------------------ verdict

Write-Host ''
if (-not (Test-Admin)) {
    Write-Info 'Note: not running elevated. Install-LhDns.ps1 and Import-LecoRootCa.ps1 need Administrator.'
}

if ($script:Blocking -gt 0) {
    Write-Host "$($script:Blocking) blocking issue(s) found." -ForegroundColor Red
    Write-Host ''
    exit 1
}

Write-Host 'Preflight passed.' -ForegroundColor Green
Write-Host ''
exit 0
