<#
.SYNOPSIS
    One-time setup of the VTFX MT5 bot on a Windows VPS.

.DESCRIPTION
    Installs Python, clones the repo, builds the virtualenv, and registers a
    scheduled task so the bot comes back by itself after a reboot.

    It deliberately does NOT install or log into MetaTrader 5. That needs a
    GUI and your broker credentials, so it stays a manual step - see README.md.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1 -EnableAutoLogon
#>
[CmdletBinding()]
param(
    [string]$RepoUrl = "https://github.com/Tronixtek/Pacifica_AI_Bot.git",
    [string]$Branch = "main",
    [string]$InstallRoot = "C:\vtfx",
    [int]$Port = 8011,
    # Windows will not run a GUI app like MT5 unless a user session exists, so a
    # rebooted VPS needs to log itself in. Opt-in because it stores the password
    # in the registry.
    [switch]$EnableAutoLogon,
    [string]$AutoLogonUser,
    [string]$AutoLogonPassword
)

$ErrorActionPreference = "Stop"
function Step { param([string]$m) Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Ok   { param([string]$m) Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn { param([string]$m) Write-Host "  [!!] $m" -ForegroundColor Yellow }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this in an elevated PowerShell (Run as Administrator)."
}

# --- prerequisites --------------------------------------------------------
Step "Checking prerequisites"
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Warn "winget not found. Install Python 3.11+ and Git manually, then re-run."
}

foreach ($pkg in @(@{id="Python.Python.3.12"; cmd="python"}, @{id="Git.Git"; cmd="git"})) {
    if (Get-Command $pkg.cmd -ErrorAction SilentlyContinue) {
        Ok "$($pkg.cmd) already present"
    } else {
        Step "Installing $($pkg.id)"
        winget install --id $pkg.id --silent --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
    }
}

# --- repo -----------------------------------------------------------------
Step "Fetching the repository"
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
$repoRoot = Join-Path $InstallRoot "Pacifica_AI_Bot"
if (Test-Path (Join-Path $repoRoot ".git")) {
    git -C $repoRoot fetch --all
    git -C $repoRoot checkout $Branch
    git -C $repoRoot pull
    Ok "Updated existing clone at $repoRoot"
} else {
    git clone --branch $Branch $RepoUrl $repoRoot
    Ok "Cloned to $repoRoot"
}

# --- virtualenv -----------------------------------------------------------
Step "Building the virtualenv"
$traderDir = Join-Path $repoRoot "services\trader"
$python = Join-Path $traderDir ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Push-Location $traderDir
    python -m venv .venv
    Pop-Location
}
& $python -m pip install --upgrade pip --quiet
& $python -m pip install -r (Join-Path $traderDir "requirements.txt") --quiet
Ok "Dependencies installed"

& $python -c "import MetaTrader5, fastapi, uvicorn; print('imports ok')"

# --- configuration --------------------------------------------------------
Step "Configuration"
$envPath = Join-Path $traderDir ".env"
if (Test-Path $envPath) {
    Ok ".env already present - leaving it alone"
} else {
    Copy-Item (Join-Path $traderDir ".env.example") $envPath
    Warn "Created .env from the example. EDIT IT before starting:"
    Warn "  BOT_MODE, MT5_LOGIN, MT5_SERVER, SYMBOLS, ENABLE_LIVE_TRADING"
    Warn "  $envPath"
}

# --- auto-logon -----------------------------------------------------------
# MT5 is a desktop application: with no interactive session it cannot run, and
# without MT5 the bot has nothing to talk to.
if ($EnableAutoLogon) {
    Step "Enabling auto-logon"
    if (-not $AutoLogonUser -or -not $AutoLogonPassword) {
        throw "-EnableAutoLogon needs -AutoLogonUser and -AutoLogonPassword."
    }
    $key = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
    Set-ItemProperty $key -Name AutoAdminLogon -Value "1"
    Set-ItemProperty $key -Name DefaultUserName -Value $AutoLogonUser
    Set-ItemProperty $key -Name DefaultPassword -Value $AutoLogonPassword
    Warn "The password is now stored in plaintext in the registry."
    Warn "Restrict RDP to your IP in the AWS security group."
    Ok "Auto-logon enabled for $AutoLogonUser"
} else {
    Warn "Auto-logon NOT enabled. After a reboot nobody is logged in, MT5 will"
    Warn "not start, and the bot will run degraded. Re-run with -EnableAutoLogon"
    Warn "unless you plan to RDP in after every reboot."
}

# --- scheduled task -------------------------------------------------------
Step "Registering the startup task"
$runner = Join-Path $repoRoot "scripts\vps\run-bot.ps1"
$taskName = "VTFX-Bot"

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -RepoRoot `"$repoRoot`" -Port $Port"
# At logon rather than at startup: the task must run inside the interactive
# session that MT5 lives in, otherwise it cannot reach the terminal.
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal | Out-Null
Ok "Scheduled task '$taskName' registered (runs at logon, restarts on failure)"

Step "Setup complete"
Write-Host @"
  Repo    : $repoRoot
  Python  : $python
  Config  : $envPath
  Task    : $taskName

  STILL TO DO BY HAND:
    1. Install MetaTrader 5 and log into your account
    2. Tick 'save password' so it reconnects after a reboot
    3. Tools > Options > Expert Advisors > Allow algorithmic trading
       (the toolbar toggle resets on restart; the Options setting persists)
    4. Close every chart and trim Market Watch to the symbols you trade
    5. Edit $envPath
    6. Start now with:  Start-ScheduledTask -TaskName $taskName

  Health check:  curl http://127.0.0.1:$Port/health
  Logs        :  $repoRoot\logs\
"@ -ForegroundColor Cyan
