<#
.SYNOPSIS
    Run LEco DevOps commands from Windows by forwarding them into WSL2.

.DESCRIPTION
    A convenience wrapper so Windows users are not typing
    `wsl -d Ubuntu -- sh -c "cd ~/local-ecosystem && ./start.sh"` every time. It resolves
    the distro and checkout once, then forwards whatever you pass.

    Nothing here reimplements platform logic — the bash scripts remain the single
    implementation, which is the point: a PowerShell port would be a second copy of the
    rules, free to drift.

.PARAMETER Command
    What to run: start, stop, restart, status, setup, dns, logs, or cli.
    Anything unrecognised is forwarded verbatim to leco-cli.sh.

.PARAMETER Rest
    Extra arguments passed through.

.EXAMPLE
    .\leco.ps1 status
    .\leco.ps1 start dashboard
    .\leco.ps1 stop
    .\leco.ps1 logs traefik
    .\leco.ps1 cli apps
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command = 'status',

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest = @()
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Host '[FAIL] WSL is not installed.' -ForegroundColor Red
    exit 1
}

# Cache the resolved distro/path for the session so every call is not two extra wsl round
# trips. Override either with an environment variable.
if (-not $env:LECO_WSL_DISTRO) {
    $raw = (& wsl.exe -l -q 2>&1 | Out-String) -replace "`0", ''
    $env:LECO_WSL_DISTRO = ($raw -split "`r?`n" | Where-Object { $_.Trim() -ne '' } | Select-Object -First 1).Trim()
}
$distro = $env:LECO_WSL_DISTRO

if (-not $env:LECO_REPO_PATH) {
    $env:LECO_REPO_PATH = (& wsl.exe -d $distro -- sh -c 'find ~ -maxdepth 3 -type d -name local-ecosystem 2>/dev/null | head -1' | Out-String).Trim()
}
$repo = $env:LECO_REPO_PATH

if (-not $repo) {
    Write-Host '[FAIL] Could not find the local-ecosystem checkout inside WSL.' -ForegroundColor Red
    Write-Host '       Set it explicitly:  $env:LECO_REPO_PATH = "/home/you/local-ecosystem"' -ForegroundColor DarkGray
    exit 1
}

$extra = if ($Rest) { ' ' + ($Rest -join ' ') } else { '' }

switch ($Command.ToLower()) {
    'start'   { $line = "./start.sh$extra" }
    'stop'    { $line = "./start.sh --stop$extra" }
    'restart' { $line = "./start.sh --restart$extra" }
    'status'  { $line = "./start.sh --status$extra" }
    'setup'   { $line = "./setup.sh$extra" }
    'dns'     { $line = "./ecosystem-stack/scripts/dns-setup.sh$extra" }
    'logs'    { $line = "./ecosystem-stack/ecosystem-stack.sh logs$extra" }
    'cli'     { $line = "./leco-cli.sh$extra" }
    default   { $line = "./leco-cli.sh $Command$extra" }
}

& wsl.exe -d $distro -- sh -c "cd '$repo' && $line"
exit $LASTEXITCODE
