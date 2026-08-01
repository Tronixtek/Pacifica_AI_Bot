<#
.SYNOPSIS
    Starts the trader backend, waiting for MetaTrader 5 to be ready first.

.DESCRIPTION
    The MetaTrader5 Python package talks to a RUNNING terminal over local IPC.
    On a VPS reboot the bot and the terminal race each other, and the bot loses:
    it starts, finds no terminal, and sits degraded until someone notices. This
    launcher blocks until the terminal is up and logged in, then starts the
    backend and restarts it if it exits.

    Intended to be run by the scheduled task created by setup.ps1, but safe to
    run by hand for debugging.
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "C:\vtfx\Pacifica_AI_Bot",
    [int]$Port = 8011,
    [int]$Mt5WaitSeconds = 300,
    [int]$RestartDelaySeconds = 15
)

$ErrorActionPreference = "Stop"
$traderDir = Join-Path $RepoRoot "services\trader"
$python = Join-Path $traderDir ".venv\Scripts\python.exe"
$logDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "supervisor.log"

function Write-Log {
    param([string]$Message)
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Output $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

if (-not (Test-Path $python)) {
    Write-Log "FATAL: no virtualenv at $python. Run setup.ps1 first."
    exit 1
}

# --- wait for the terminal ------------------------------------------------
# Presence of the process is not enough: the terminal takes time to connect to
# the broker, and account_info() returns nothing until it has.
$probe = @'
import sys, time
try:
    import MetaTrader5 as mt5
except ImportError:
    print("NO_PACKAGE"); sys.exit(2)
if not mt5.initialize():
    print("NO_TERMINAL"); sys.exit(3)
a = mt5.account_info(); t = mt5.terminal_info()
if a is None:
    print("NOT_LOGGED_IN"); mt5.shutdown(); sys.exit(4)
print(f"READY login={a.login} server={a.server} balance={a.balance} autotrading={t.trade_allowed}")
mt5.shutdown()
'@
$probePath = Join-Path $env:TEMP "vtfx_mt5_probe.py"
Set-Content -Path $probePath -Value $probe -Encoding utf8

Write-Log "Waiting up to $Mt5WaitSeconds s for MetaTrader 5..."
$deadline = (Get-Date).AddSeconds($Mt5WaitSeconds)
$ready = $false
while ((Get-Date) -lt $deadline) {
    $out = & $python $probePath 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Log "MT5 $out"
        if ($out -match "autotrading=False") {
            Write-Log "WARNING: AutoTrading is OFF. Orders will be rejected with retcode 10027."
            Write-Log "         Enable it in Tools > Options > Expert Advisors so it survives reboots."
        }
        $ready = $true
        break
    }
    Write-Log "MT5 not ready ($out). Retrying in 10s..."
    Start-Sleep -Seconds 10
}

if (-not $ready) {
    # Start anyway: the engine reports market_data as degraded and retries, which
    # is more useful than the task exiting and leaving nothing running.
    Write-Log "MT5 still not ready after $Mt5WaitSeconds s. Starting the backend regardless."
}

# --- supervise the backend ------------------------------------------------
Write-Log "Starting backend on port $Port"
while ($true) {
    $started = Get-Date
    Push-Location $traderDir
    try {
        & $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port 2>&1 |
            ForEach-Object { Add-Content -Path (Join-Path $logDir "backend.log") -Value $_ -Encoding utf8 }
    } catch {
        Write-Log "Backend threw: $_"
    } finally {
        Pop-Location
    }
    $ranFor = [int]((Get-Date) - $started).TotalSeconds
    Write-Log "Backend exited after ${ranFor}s. Restarting in $RestartDelaySeconds s."
    # A process that dies immediately is misconfigured, not merely unlucky.
    # Backing off avoids a hot restart loop filling the disk with logs.
    if ($ranFor -lt 30) {
        Write-Log "Exited almost immediately - backing off 60s. Check backend.log."
        Start-Sleep -Seconds 60
    }
    Start-Sleep -Seconds $RestartDelaySeconds
}
