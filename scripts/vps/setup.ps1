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

function Update-PathFromRegistry {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Test-RealPython {
    <#
      Windows ships an App Execution Alias at
      %LOCALAPPDATA%\Microsoft\WindowsApps\python.exe. It sits on PATH and
      satisfies Get-Command, but it is only a stub that redirects to the
      Microsoft Store: running it prints "Python was not found" and creates
      nothing. Only a real interpreter answers --version with a version.
    #>
    param([string]$Exe)
    if (-not $Exe) { return $false }
    try {
        $out = & $Exe --version 2>&1 | Out-String
        return ($LASTEXITCODE -eq 0 -and $out -match "Python\s+3\.(1[1-9]|[2-9][0-9])")
    } catch {
        return $false
    }
}

function Resolve-Python {
    # The py launcher is tried first: it ships with the official distribution,
    # is never shadowed by the Store alias, and knows where real interpreters
    # are installed.
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $found = & py -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { $candidates += $found.Trim() }
    }
    $candidates += (Get-Command python -All -ErrorAction SilentlyContinue |
                    Where-Object { $_.Source -and $_.Source -notlike "*\WindowsApps\*" } |
                    Select-Object -ExpandProperty Source)
    $candidates += Get-ChildItem -Path @(
        "$env:LOCALAPPDATA\Programs\Python",
        "$env:ProgramFiles\Python*",
        "C:\Python*"
    ) -Filter python.exe -Recurse -Depth 2 -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName

    foreach ($c in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if (Test-RealPython $c) { return $c }
    }
    return $null
}

$pythonExe = Resolve-Python
if ($pythonExe) {
    Ok "Python found: $pythonExe ($(& $pythonExe --version 2>&1))"
} else {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "No usable Python 3.11+ and winget is unavailable. Install Python from python.org, tick 'Add python.exe to PATH', then re-run."
    }
    Step "Installing Python 3.12"
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    Update-PathFromRegistry
    $pythonExe = Resolve-Python
    if (-not $pythonExe) {
        throw "Python installed but could not be located. Close this window, open a NEW elevated PowerShell, and re-run."
    }
    Ok "Python installed: $pythonExe"
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Step "Installing Git"
    winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements
    Update-PathFromRegistry
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git installed but is not on PATH. Open a NEW elevated PowerShell and re-run."
    }
}
Ok "git present"

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
    # Use the resolved interpreter, not whatever `python` happens to mean on
    # PATH - that is how the Store stub got in here in the first place.
    & $pythonExe -m venv .venv
    Pop-Location
}
if (-not (Test-Path $python)) {
    throw "venv creation failed - no interpreter at $python"
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
