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
    # Loopback by default. Widen ONLY to an address that is itself private - a
    # Tailscale 100.x address, say. Never 0.0.0.0: the API has no
    # authentication and can place trades.
    [string]$BindAddress = "127.0.0.1",
    # How long to wait for a non-loopback bind address to appear on an
    # interface before giving up and using loopback.
    [int]$BindWaitSeconds = 120,
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

# --- refuse to start beside another instance ------------------------------
# uvicorn runs application startup BEFORE it binds the port, so a second
# instance boots all three trading engines and can place orders before dying
# on the bind error. Supervised, that repeats every 60 seconds: a shadow set
# of bots trading the same account under the same magic numbers, invisible to
# the first instance's position limits. Refuse rather than race.
$holder = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
          Select-Object -First 1
if ($holder) {
    $owner = Get-Process -Id $holder.OwningProcess -ErrorAction SilentlyContinue
    Write-Log "FATAL: port $Port is already held by PID $($holder.OwningProcess) ($($owner.ProcessName))."
    Write-Log "       Another instance is running. Stop it before starting this one:"
    Write-Log "         Stop-Process -Id $($holder.OwningProcess) -Force"
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

# --- wait for the bind address to exist -----------------------------------
# A specific address can only be bound once the interface carrying it is up.
# Tailscale negotiates its address asynchronously after boot, so the scheduled
# task can reach this point before 100.x exists and uvicorn dies with
# "could not bind on any address". Wait for it, then fall back to loopback so
# the bots keep trading even if the tailnet never comes up - a dashboard that
# is only reachable from the VPS beats no trading at all.
if ($BindAddress -ne "127.0.0.1" -and $BindAddress -ne "0.0.0.0") {
    Write-Log "Waiting up to ${BindWaitSeconds}s for $BindAddress to be assigned..."
    $deadline = (Get-Date).AddSeconds($BindWaitSeconds)
    $assigned = $false
    while ((Get-Date) -lt $deadline) {
        if (Get-NetIPAddress -IPAddress $BindAddress -ErrorAction SilentlyContinue) {
            Write-Log "$BindAddress is up"
            $assigned = $true
            break
        }
        Start-Sleep -Seconds 5
    }
    if (-not $assigned) {
        Write-Log "WARNING: $BindAddress never appeared. Is Tailscale connected?"
        Write-Log "         Falling back to 127.0.0.1 so trading continues."
        Write-Log "         The dashboard will only be reachable from this machine"
        Write-Log "         until the tailnet is up and the service is restarted."
        $BindAddress = "127.0.0.1"
    }
}

# --- supervise the backend ------------------------------------------------
Write-Log "Starting backend on ${BindAddress}:${Port}"
if ($BindAddress -eq "0.0.0.0") {
    Write-Log "WARNING: binding 0.0.0.0 exposes an unauthenticated trading API to"
    Write-Log "         every network this machine is on. Use a private address."
}
while ($true) {
    $started = Get-Date
    # Start-Process with explicit redirects, NOT `& ... 2>&1 | ForEach-Object`.
    # In PowerShell 5.1 that pipeline wraps every stderr line from a native
    # executable in an ErrorRecord and can drop the output altogether - which
    # is how the backend crashed repeatedly while backend.log was never even
    # created, leaving the failure invisible.
    $outLog = Join-Path $logDir "backend.log"
    $errLog = Join-Path $logDir "backend.err.log"
    try {
        $proc = Start-Process -FilePath $python `
            -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "$BindAddress", "--port", "$Port") `
            -WorkingDirectory $traderDir `
            -RedirectStandardOutput $outLog `
            -RedirectStandardError $errLog `
            -NoNewWindow -PassThru
        $proc.WaitForExit()
        $exitCode = $proc.ExitCode
    } catch {
        Write-Log "Could not launch the backend: $_"
        $exitCode = -1
    }

    # Surface the tail of stderr into the supervisor log, so one file tells the
    # whole story rather than pointing at another that may not exist.
    if ((Test-Path $errLog) -and (Get-Item $errLog).Length -gt 0) {
        Write-Log "--- backend stderr (last 15 lines) ---"
        Get-Content $errLog -Tail 15 | ForEach-Object { Write-Log "    $_" }
        Write-Log "--- end stderr ---"
    }
    Write-Log "Backend exit code: $exitCode"
    $ranFor = [int]((Get-Date) - $started).TotalSeconds

    # If something else grabbed the port while we were down, stop entirely.
    # Restarting into a taken port is the loop that produces shadow bots.
    $other = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
             Select-Object -First 1
    if ($other) {
        Write-Log "Port $Port is now held by PID $($other.OwningProcess). Another instance"
        Write-Log "took over; exiting rather than competing for the same account."
        exit 1
    }

    Write-Log "Backend exited after ${ranFor}s. Restarting in $RestartDelaySeconds s."
    # A process that dies immediately is misconfigured, not merely unlucky.
    # Backing off avoids a hot restart loop filling the disk with logs.
    if ($ranFor -lt 30) {
        Write-Log "Exited almost immediately - backing off 60s. Check backend.log."
        Start-Sleep -Seconds 60
    }
    Start-Sleep -Seconds $RestartDelaySeconds
}
