# Run this from inside the Windows VM/PC, from the repo root:
#   powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
#
# Sets up the trader backend's virtualenv and .env file. Does not start
# anything and does not touch MT5 credentials beyond copying the template.

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$traderDir = Join-Path $repoRoot "services\trader"

function Assert-Command($name, $friendlyName) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Error "$friendlyName was not found on PATH. Install it and re-run this script."
        exit 1
    }
}

Write-Host "Checking prerequisites..." -ForegroundColor Cyan
Assert-Command "python" "Python"

$pythonVersion = (python --version) 2>&1
Write-Host "Found $pythonVersion"

Push-Location $traderDir
try {
    if (-not (Test-Path ".venv")) {
        Write-Host "Creating virtualenv..." -ForegroundColor Cyan
        python -m venv .venv
    } else {
        Write-Host "Virtualenv already exists, skipping creation."
    }

    $pip = ".\.venv\Scripts\pip.exe"
    $python = ".\.venv\Scripts\python.exe"

    Write-Host "Installing dependencies (this installs the real MetaTrader5 package here)..." -ForegroundColor Cyan
    & $python -m pip install --upgrade pip
    & $pip install -r requirements.txt

    Write-Host "Confirming MetaTrader5 imports..." -ForegroundColor Cyan
    & $python -c "import MetaTrader5 as mt5; print('MetaTrader5 package version:', mt5.__version__)"

    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Write-Host ""
        Write-Host "Created .env from .env.example. Edit it now and fill in:" -ForegroundColor Yellow
        Write-Host "  BOT_MODE=demo"
        Write-Host "  MT5_LOGIN=<your demo account number>"
        Write-Host "  MT5_PASSWORD=<your demo password>"
        Write-Host "  MT5_SERVER=<your broker's demo server, e.g. ICMarkets-Demo>"
        Write-Host "  USE_SIMULATED_FEED=false"
    } else {
        Write-Host ".env already exists, leaving it as-is."
    }

    Write-Host ""
    Write-Host "Setup complete. Next steps:" -ForegroundColor Green
    Write-Host "  1. Make sure the MT5 terminal is open and logged into your demo account."
    Write-Host "  2. Edit services\trader\.env with your MT5_LOGIN / MT5_PASSWORD / MT5_SERVER."
    Write-Host "  3. From services\trader, run:"
    Write-Host "       .\.venv\Scripts\Activate.ps1"
    Write-Host "       uvicorn app.main:app --reload"
    Write-Host "  4. Check http://127.0.0.1:8000/api/diagnostics?live_probe=true for mt5Connected: true"
} finally {
    Pop-Location
}
