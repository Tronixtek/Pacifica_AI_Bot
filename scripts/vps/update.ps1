<#
.SYNOPSIS
    Pull the latest code and restart the bots. Safe to run repeatedly.

.DESCRIPTION
    The day-to-day command. Stops the bots, fetches the branch, reinstalls
    dependencies if they changed, restarts, waits for health, and prints each
    bot's performance.

    Deliberately refuses to run while positions are open unless -Force is
    given: restarting mid-trade abandons in-memory trailing state, and the
    position then rides to its original broker-side stop with nothing managing
    it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File update.ps1
    powershell -ExecutionPolicy Bypass -File update.ps1 -Branch multi-bot -Force
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "C:\vtfx\Pacifica_AI_Bot",
    [string]$Branch = "",
    [int]$Port = 8011,
    [string]$TaskName = "VTFX-Bot",
    [switch]$Force,
    [switch]$SkipRestart
)

$ErrorActionPreference = "Stop"
function Step { param([string]$m) Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Ok   { param([string]$m) Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn { param([string]$m) Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Info { param([string]$m) Write-Host "  $m" -ForegroundColor Gray }

$trader = Join-Path $RepoRoot "services\trader"
$python = Join-Path $trader ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "No virtualenv at $python. Run setup.ps1 first." }

# --- refuse to restart on top of live positions ---------------------------
Step "Checking for open positions"
$posScript = @'
import sys
try:
    import MetaTrader5 as mt5
except ImportError:
    print("0"); sys.exit(0)
if not mt5.initialize():
    print("0"); sys.exit(0)
pos = mt5.positions_get() or []
print(len(pos))
for p in pos:
    print(f"   {p.symbol} {p.volume} lots magic={p.magic} pnl={p.profit:+.2f}", file=sys.stderr)
mt5.shutdown()
'@
$probe = Join-Path $env:TEMP "vtfx_positions.py"
Set-Content -Path $probe -Value $posScript -Encoding utf8
$openCount = 0
try { $openCount = [int](& $python $probe 2>$null | Select-Object -First 1) } catch { $openCount = 0 }

if ($openCount -gt 0) {
    Warn "$openCount position(s) are open."
    & $python $probe 2>&1 | Select-Object -Skip 1 | ForEach-Object { Info $_ }
    if (-not $Force) {
        Warn "Not restarting. Their broker-side stops stay in place, but the"
        Warn "trailing logic is in memory and would be lost."
        Warn "Re-run with -Force to restart anyway."
        exit 2
    }
    Warn "-Force given; restarting with positions open."
} else {
    Ok "No open positions"
}

# --- stop ------------------------------------------------------------------
if (-not $SkipRestart) {
    Step "Stopping the bots"
    try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
            Ok "Stopped PID $($_.OwningProcess)"
        }
    # Supervisor and child are separate processes; catch the supervisor too.
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*app.main*" -or $_.CommandLine -like "*app.scalper*" } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Ok "Stopped stray PID $($_.ProcessId)"
        }
    Start-Sleep -Seconds 2
}

# --- pull ------------------------------------------------------------------
Step "Fetching the latest code"
Push-Location $RepoRoot
try {
    $before = (git rev-parse HEAD).Trim()
    git fetch --all --quiet
    if ($Branch) { git checkout $Branch --quiet }
    $current = (git rev-parse --abbrev-ref HEAD).Trim()
    git pull --quiet
    $after = (git rev-parse HEAD).Trim()
    if ($before -eq $after) {
        Ok "Already up to date on $current ($($after.Substring(0,7)))"
    } else {
        Ok "Updated $current : $($before.Substring(0,7)) -> $($after.Substring(0,7))"
        git log --oneline "$before..$after" | ForEach-Object { Info $_ }
    }
    $reqChanged = $before -ne $after -and
        (git diff --name-only $before $after | Where-Object { $_ -like "*requirements.txt" })
} finally {
    Pop-Location
}

# --- dependencies ----------------------------------------------------------
if ($reqChanged -or $Force) {
    Step "Installing dependencies"
    & $python -m pip install -r (Join-Path $trader "requirements.txt") --quiet
    Ok "Dependencies up to date"
} else {
    Info "requirements.txt unchanged; skipping pip"
}

& $python -c "import MetaTrader5, fastapi, uvicorn; print('  [ok] imports fine')"

# --- config drift ----------------------------------------------------------
Step "Checking configuration"
$envFile = Join-Path $trader ".env"
$example  = Join-Path $trader ".env.example"
if (-not (Test-Path $envFile)) {
    Copy-Item $example $envFile
    Warn "No .env existed; created one from the example. EDIT IT before trading."
} else {
    # New settings ship in .env.example and fall back to code defaults if the
    # live .env lacks them, which is easy to miss. Name them explicitly.
    $have = (Get-Content $envFile) -match '^\s*[A-Z0-9_]+=' |
            ForEach-Object { ($_ -split '=')[0].Trim() }
    $want = (Get-Content $example) -match '^\s*[A-Z0-9_]+=' |
            ForEach-Object { ($_ -split '=')[0].Trim() }
    $missing = $want | Where-Object { $have -notcontains $_ }
    if ($missing) {
        Warn "New settings in .env.example that your .env does not set:"
        $missing | ForEach-Object { Info "  $_" }
        Info "(defaults from code apply until you add them)"
    } else {
        Ok ".env covers every documented setting"
    }
}

# --- validate the config before restarting --------------------------------
# A malformed .env crashes the backend one second after launch, and the
# supervisor then retries on a 60s backoff. Without this check the only
# symptom is a health wait that times out for reasons nothing surfaces.
Step "Validating the configuration"
# Written to a file rather than passed with -c. Quoting a Python one-liner
# through PowerShell's native-command handling is unreliable: escaped quotes
# do not survive, and the failure looks like a Python syntax error rather than
# a quoting problem.
$checkScript = @'
import os
import sys

# Python puts the SCRIPT's directory on sys.path, not the working directory.
# This file lives in %TEMP%, so `app` is only importable once the trader
# directory the caller chdir'd into is added explicitly.
sys.path.insert(0, os.getcwd())

try:
    from app.config import Settings
    s = Settings()
except Exception as exc:
    print(f"CONFIG_ERROR: {type(exc).__name__}: {exc}")
    sys.exit(1)
print(f"mode={s.botMode} symbols={','.join(s.symbols)} live={s.enableLiveTrading}")
print(f"setups={','.join(s.enabledSetups)} timeframe={s.strategyTimeframe} ml={s.mlEnabled}")
print(f"bots: price_action={s.mt5MagicNumber}/{s.maxOpenPositions} "
      f"scalper={s.scalperMagicNumber}/{s.scalperMaxOpenPositions} "
      f"crt={s.crtMagicNumber}/{s.crtMaxOpenPositions}")
'@
$checkPath = Join-Path $env:TEMP "vtfx_config_check.py"
Set-Content -Path $checkPath -Value $checkScript -Encoding ascii

Push-Location $trader
try {
    $check = & $python $checkPath 2>&1
    $failed = ($LASTEXITCODE -ne 0) -or ("$check" -match "CONFIG_ERROR")
    if ($failed) {
        Warn "The configuration does not load:"
        $check | ForEach-Object { Info "  $_" }
        Warn "Rewrite it with:"
        Info "  powershell -ExecutionPolicy Bypass -File $RepoRoot\scripts\vps\configure.ps1 ``"
        Info "      -Login <login> -Server <server> -EnableLiveTrading -Force"
        exit 1
    }
    $check | ForEach-Object { Ok $_ }
} finally {
    Pop-Location
}

if ($SkipRestart) { Step "Done (restart skipped)"; exit 0 }

# --- start -----------------------------------------------------------------
Step "Starting the bots"
try {
    Start-ScheduledTask -TaskName $TaskName
    Ok "Scheduled task '$TaskName' started"
} catch {
    Warn "Could not start the task ($_). Launching directly instead."
    $runner = Join-Path $RepoRoot "scripts\vps\run-bot.ps1"
    Start-Process powershell -ArgumentList @(
        "-NoProfile","-ExecutionPolicy","Bypass","-WindowStyle","Hidden",
        "-File","`"$runner`"","-RepoRoot","`"$RepoRoot`"","-Port",$Port
    )
}

Step "Waiting for the service"
# This must outlast run-bot.ps1's own wait for MetaTrader 5 (300s by default).
# A shorter wait here reports failure while the supervisor is still waiting,
# and the backend then comes up unattended a few minutes later.
$supervisorLog = Join-Path $RepoRoot "logs\supervisor.log"
$healthy = $false
$lastSeen = ""
foreach ($i in 1..130) {                     # ~6.5 minutes
    Start-Sleep -Seconds 3
    try {
        $r = Invoke-RestMethod "http://127.0.0.1:$Port/health" -TimeoutSec 4
        Write-Host ""
        Ok "Healthy: status=$($r.status) mode=$($r.mode) liveTrading=$($r.liveTradingEnabled)"
        $healthy = $true
        break
    } catch {
        # Echo what the supervisor is doing instead of printing dots. The usual
        # answer is that MT5 is not running, which waiting will never fix.
        $line = if (Test-Path $supervisorLog) {
            Get-Content $supervisorLog -Tail 1 -ErrorAction SilentlyContinue
        } else { $null }
        if ($line -and $line -ne $lastSeen) {
            Write-Host ""
            Info $line
            $lastSeen = $line
        } else {
            Write-Host "." -NoNewline
        }
    }
}
if (-not $healthy) {
    Write-Host ""
    Warn "No response after 6 minutes."
    if (Test-Path $supervisorLog) {
        Warn "Last lines from the supervisor:"
        Get-Content $supervisorLog -Tail 12 | ForEach-Object { Info "  $_" }
    }
    Warn "The usual cause is MetaTrader 5 not running or not logged in. Check with:"
    Info "  cd $trader"
    Info "  .\.venv\Scripts\python.exe -c ""import MetaTrader5 as m; print(m.initialize()); print(m.account_info())"""
    Info "  Get-Content $RepoRoot\logs\backend.log -Tail 60"
    exit 1
}

# --- report ----------------------------------------------------------------
Step "Bot performance"
try {
    $fleet = Invoke-RestMethod "http://127.0.0.1:$Port/api/bots" -TimeoutSec 10
    Write-Host ("  {0,-32} {1,7} {2,8} {3,11} {4,6}" -f "BOT","TRADES","WIN%","REALISED","OPEN")
    Write-Host ("  " + ("-" * 68))
    foreach ($b in $fleet.bots) {
        Write-Host ("  {0,-32} {1,7} {2,7:N1}% {3,10:N2} {4,6}" -f
            $b.label, $b.trades, $b.winRate, $b.realisedUsd, $b.openPositions)
    }
    if ($null -ne $fleet.accountEquityUsd) {
        Write-Host ("`n  equity {0:N2}  balance {1:N2}  open {2}" -f
            $fleet.accountEquityUsd, $fleet.accountBalanceUsd, $fleet.openPositions)
    }
} catch {
    Warn "Could not read /api/bots: $_"
}

Write-Host @"

  Dashboard : http://127.0.0.1:$Port   (loopback only, by design)
  From your machine:
      ssh -N -L ${Port}:127.0.0.1:$Port Administrator@<vps-ip>
    then open http://127.0.0.1:$Port locally.
  Run enable-remote-access.ps1 once if SSH is not set up yet.
"@ -ForegroundColor Cyan
