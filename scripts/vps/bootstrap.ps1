<#
.SYNOPSIS
    Switch the VPS clone to a branch, then hand over to that branch's update.ps1.

.DESCRIPTION
    Solves a bootstrapping problem: update.ps1 and setup.ps1 live IN the branch
    you are moving to, so running them before the checkout fails with "the
    argument to -File does not exist". This script does the git work with no
    dependency on branch contents, then delegates.

    Kept deliberately small and on every branch, so it is always present
    whatever the clone currently has checked out.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File bootstrap.ps1 -Branch multi-bot
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Branch,
    [string]$RepoRoot = "C:\vtfx\Pacifica_AI_Bot",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
function Ok   { param([string]$m) Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn { param([string]$m) Write-Host "  [!!] $m" -ForegroundColor Yellow }

if (-not (Test-Path (Join-Path $RepoRoot ".git"))) {
    throw "No git repository at $RepoRoot. Run setup.ps1 first."
}

Write-Host "`n=== Switching to $Branch ===" -ForegroundColor Cyan
Push-Location $RepoRoot
try {
    git fetch --all --quiet
    # Local edits to .env are expected and must not block the checkout; .env is
    # gitignored, so anything tracked and dirty here is unintended.
    $dirty = git status --porcelain
    if ($dirty -and -not $Force) {
        Warn "Tracked files have local changes:"
        $dirty | ForEach-Object { Write-Host "     $_" }
        Warn "Commit, stash, or re-run with -Force to discard them."
        exit 2
    }
    if ($dirty -and $Force) {
        git reset --hard HEAD --quiet
        Warn "Discarded local changes to tracked files"
    }
    git checkout $Branch --quiet
    git pull --quiet
    Ok "On $(git rev-parse --abbrev-ref HEAD) at $((git rev-parse HEAD).Substring(0,7))"
} finally {
    Pop-Location
}

$update = Join-Path $RepoRoot "scripts\vps\update.ps1"
if (Test-Path $update) {
    Write-Host "`n=== Handing over to update.ps1 ===" -ForegroundColor Cyan
    & powershell -NoProfile -ExecutionPolicy Bypass -File $update -RepoRoot $RepoRoot @(
        if ($Force) { "-Force" }
    )
} else {
    Warn "$Branch has no scripts/vps/update.ps1; nothing further to run."
    Warn "The checkout succeeded, so start the bots however that branch expects."
}
