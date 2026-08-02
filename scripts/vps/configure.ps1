<#
.SYNOPSIS
    Write a complete, valid .env for the trader service.

.DESCRIPTION
    Exists because pasting a multi-line here-string into an interactive
    PowerShell console does not reliably terminate: `"@` must sit at column 0,
    and a paste that indents it silently swallows the rest of the command into
    the string. The result is a .env containing the command meant to write it,
    and a backend that dies on startup with a dotenv parse error.

    Writing the file from a script removes that whole class of problem. Values
    are single-quoted literals in an array, so nothing is interpolated or
    line-continued.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File configure.ps1 `
        -Login 476096391 -Server Exness-MT5Trial9 -Symbols "BTCUSD,GBPUSD,EURUSD"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][long]$Login,
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$Symbols = "BTCUSD,GBPUSD,EURUSD",
    [string]$ScalperSymbol = "BTCUSD",
    [double]$StartingEquityUsd = 500,
    [string]$RepoRoot = "C:\vtfx\Pacifica_AI_Bot",
    # Off by default: writing a config should not silently arm live trading.
    [switch]$EnableLiveTrading,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
function Ok   { param([string]$m) Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn { param([string]$m) Write-Host "  [!!] $m" -ForegroundColor Yellow }

$trader = Join-Path $RepoRoot "services\trader"
$envPath = Join-Path $trader ".env"
$python = Join-Path $trader ".venv\Scripts\python.exe"

if ((Test-Path $envPath) -and -not $Force) {
    Warn "$envPath already exists. Re-run with -Force to overwrite."
    exit 2
}
if (Test-Path $envPath) {
    $backup = "$envPath.bak"
    Copy-Item $envPath $backup -Force
    Ok "Backed up the previous config to $backup"
}

$live = if ($EnableLiveTrading) { 'true' } else { 'false' }

# Single-quoted literals: no interpolation, no continuation, nothing for a
# paste or a shell to reinterpret.
$lines = @(
    '# Written by scripts/vps/configure.ps1',
    'APP_ENV=production',
    'SERVICE_NAME=vtfx-mt5-trader',
    'LOG_LEVEL=INFO',
    'LOG_FORMAT=json',
    'AUDIT_LOG_PATH=logs/audit.jsonl',
    '',
    'BOT_MODE=demo',
    "MT5_LOGIN=$Login",
    '# Blank on purpose: the bots attach to the already-logged-in terminal.',
    '# MT5_LOGIN above is a guard - startup fails if the terminal is on a',
    '# different account, which is what stops a live account being traded by',
    '# accident.',
    'MT5_PASSWORD=',
    "MT5_SERVER=$Server",
    'MT5_TERMINAL_PATH=',
    'MT5_MAGIC_NUMBER=990211',
    'MT5_DEVIATION_POINTS=20',
    'MT5_CONNECT_TIMEOUT_MS=10000',
    '',
    'SYMBOL_SUFFIX=',
    "SYMBOLS=$Symbols",
    'USE_SIMULATED_FEED=false',
    "ENABLE_LIVE_TRADING=$live",
    'POLL_INTERVAL_SEC=2.0',
    'ACCOUNT_SYNC_INTERVAL_SEC=10.0',
    'MARKET_DATA_STALE_AFTER_SEC=15.0',
    '',
    '# --- price action bot (magic 990211) ---',
    "STARTING_EQUITY_USD=$StartingEquityUsd",
    'MAX_RISK_PER_TRADE_PCT=0.75',
    'MIN_SIGNAL_CONFIDENCE=0.72',
    'MAX_DAILY_LOSS_PCT=3.0',
    'ENFORCE_DAILY_LOSS_LIMIT=true',
    'MAX_OPEN_POSITIONS=5',
    'CONTRARIAN_EXECUTION_ENABLED=true',
    'CONTRARIAN_TARGET_RISK_MULTIPLE=0.25',
    'TRAILING_STOP_ENABLED=true',
    'TRAIL_ACTIVATE_R=0.25',
    'TRAIL_ATR_MULTIPLE=0.5',
    'STRATEGY_TIMEFRAME=15m',
    'STRATEGY_BAR_COUNT=400',
    'BAR_REFRESH_INTERVAL_SEC=15.0',
    'MAX_BAR_AGE_FRACTION=0.5',
    'MAX_ENTRY_DRIFT_RISK_FRACTION=0.25',
    'ENABLED_SETUPS=breakout',
    'ML_ENABLED=false',
    'PERSIST_RUNTIME_STATE=true',
    '',
    '# --- scalper bot (magic 990212) ---',
    'SCALPER_ENABLED=true',
    "SCALPER_SYMBOL=$ScalperSymbol",
    'SCALPER_TARGET_USD=0.20',
    'SCALPER_STOP_USD=4.00',
    'SCALPER_SIDE=alternate',
    'SCALPER_MAX_OPEN_POSITIONS=5',
    'SCALPER_MAGIC_NUMBER=990212',
    'SCALPER_MAX_DRAWDOWN_USD=25.0',
    'SCALPER_MAX_CONSECUTIVE_LOSSES=5',
    'SCALPER_MAX_TRADES=0',
    '',
    '# --- CRT bot (magic 990213) ---',
    'CRT_ENABLED=true',
    'CRT_MAGIC_NUMBER=990213',
    'CRT_EXECUTION_TIMEFRAME=5m',
    'CRT_RANGE_FACTOR=3',
    'CRT_MAX_OPEN_POSITIONS=5',
    'CRT_RISK_PER_TRADE_PCT=0.5',
    'CRT_MIN_SWEEP_ATR=0.1',
    'CRT_STOP_BUFFER_ATR=0.25',
    'CRT_TRAIL_ACTIVATE_R=0.25',
    'CRT_TRAIL_ATR_MULTIPLE=0.5'
)

# ASCII, not utf8: PowerShell 5.1 writes a BOM for utf8, and a BOM at the head
# of a .env is another way to get an unhelpful dotenv parse error.
Set-Content -Path $envPath -Value $lines -Encoding ascii
Ok "Wrote $envPath ($($lines.Count) lines)"

Write-Host "`n=== Validating ===" -ForegroundColor Cyan
if (-not (Test-Path $python)) {
    Warn "No virtualenv yet; skipping validation. Run setup.ps1 first."
    exit 0
}
Push-Location $trader
try {
    $out = & $python -c "from app.config import Settings; s=Settings(); print(f'mode={s.botMode} symbols={s.symbols} live={s.enableLiveTrading} setups={s.enabledSetups}'); print(f'bots: price_action={s.mt5MagicNumber}/{s.maxOpenPositions} scalper={s.scalperMagicNumber}/{s.scalperMaxOpenPositions} crt={s.crtMagicNumber}/{s.crtMaxOpenPositions}')" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Warn "The config still does not load:"
        $out | ForEach-Object { Write-Host "    $_" }
        exit 1
    }
    $out | ForEach-Object { Ok $_ }
} finally {
    Pop-Location
}

if (-not $EnableLiveTrading) {
    Write-Host "`n  ENABLE_LIVE_TRADING is false - the bots will observe but place no orders." -ForegroundColor Yellow
    Write-Host "  Re-run with -EnableLiveTrading when you want them trading.`n" -ForegroundColor Yellow
}
