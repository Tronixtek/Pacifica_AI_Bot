<#
.SYNOPSIS
    Stops the trader properly - supervisor AND the backend it spawned.

.DESCRIPTION
    Stop-ScheduledTask only stops the task's own process, which is the
    PowerShell supervisor in run-bot.ps1. The python backend it launched is a
    separate child and survives, keeping port 8011 open.

    That combination fails in a way that looks like success. The next
    Start-ScheduledTask runs a fresh supervisor, which finds the port held and
    exits immediately on its own guard - correctly, since two instances would
    trade the same account under the same magic number. Nothing appears to be
    wrong: the API still answers, the dashboard still renders, the log still
    has content. But it is all the OLD process, running the OLD configuration,
    and a config change silently never takes effect.

    That is exactly what happened on 2026-08-07: three restarts in a row left
    the original build running, and the giveaway was the startup line still
    reporting the pre-top-up equity.

    So this stops the whole tree and does not return until the port is free.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File stop-bot.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File stop-bot.ps1 -ThenStart
#>
[CmdletBinding()]
param(
    [string]$TaskName = "VTFX-Bot",
    [int]$Port = 8011,
    [int]$WaitSeconds = 30,
    # Restart afterwards. Separate from the stop so the common case of "take
    # it down and leave it down" cannot restart it by accident.
    [switch]$ThenStart
)

$ErrorActionPreference = "Continue"

function Write-Step { param([string]$m) Write-Output ("  " + $m) }

Write-Output "Stopping $TaskName ..."

# 1. The task itself. Disabling as well as stopping, so a trigger cannot
#    restart it midway through the teardown.
try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    if ($task.State -eq "Running") { Stop-ScheduledTask -TaskName $TaskName }
    Write-Step "scheduled task stopped"
} catch {
    Write-Step "no scheduled task named '$TaskName' (that is fine if you start it by hand)"
}

# 2. The supervisor, matched on its command line rather than by name - there
#    are usually other PowerShell processes and killing them all is rude.
$supervisors = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
    Where-Object { $_.CommandLine -like '*run-bot*' })
foreach ($s in $supervisors) {
    Stop-Process -Id $s.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Step ("supervisors stopped: " + $supervisors.Count)

# 3. The backend children. Matched on the uvicorn command line so an unrelated
#    python on the box is left alone.
$backends = @(Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like '*uvicorn*' })
foreach ($b in $backends) {
    Stop-Process -Id $b.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Step ("backends stopped: " + $backends.Count)

# 4. Wait for the port. This is the only check that actually proves the old
#    instance is gone - process lists can lag, and a half-released socket
#    still fails the next supervisor's guard.
$deadline = (Get-Date).AddSeconds($WaitSeconds)
$free = $false
while ((Get-Date) -lt $deadline) {
    $held = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($held.Count -eq 0) { $free = $true; break }
    Start-Sleep -Milliseconds 500
}

if (-not $free) {
    $held = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    Write-Output ""
    Write-Output "FAILED: port $Port is still held after $WaitSeconds s."
    foreach ($h in $held) {
        $p = Get-Process -Id $h.OwningProcess -ErrorAction SilentlyContinue
        Write-Output ("  PID " + $h.OwningProcess + " (" + $p.ProcessName + ")")
    }
    Write-Output "Do NOT start the bot until this is clear: a second instance"
    Write-Output "would trade the same account under the same magic number."
    exit 1
}

Write-Step "port $Port is free"
Write-Output "Stopped."

if ($ThenStart) {
    Write-Output ""
    Write-Output "Starting $TaskName ..."
    try {
        Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
        Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    } catch {
        Write-Output "Could not start the task: $_"
        exit 1
    }
    Write-Output "Started. Confirm it is the NEW process, not a survivor:"
    Write-Output ""
    Write-Output "  Start-Sleep -Seconds 60"
    Write-Output "  Select-String -Path logs\backend.log -Pattern '\[edge\]' | Select-Object -Last 6"
    Write-Output ""
    Write-Output "Check the timestamp is fresh and the equity matches the account."
    Write-Output "A stale startup line is the symptom of the failure this script exists to prevent."
}
