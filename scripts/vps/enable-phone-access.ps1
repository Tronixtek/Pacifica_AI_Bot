<#
.SYNOPSIS
    Reach the dashboard from a phone, anywhere, without exposing anything.

.DESCRIPTION
    Installs Tailscale and rebinds the service to the private Tailscale
    address, so your phone can load the dashboard over an encrypted mesh
    while the API stays unreachable from the internet.

    Why not simply open the port: the API has no authentication and POST
    /api/operator/test-order places a real trade. A public 8011 is found by
    scanners within hours. Tailscale gives a stable private address reachable
    only by devices you have signed in - the network itself is the
    authentication.

    Also sets API_READ_ONLY=true. Even inside a private network the trading
    endpoints have no business being reachable from a phone, and a mistyped
    URL or a stale browser tab should not be able to open a position.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File enable-phone-access.ps1
    powershell -ExecutionPolicy Bypass -File enable-phone-access.ps1 -KeepTradingEndpoints
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "C:\vtfx\Pacifica_AI_Bot",
    [int]$Port = 8011,
    [string]$TaskName = "VTFX-Bot",
    # Leave the operator endpoints enabled. Only sensible if you intend to
    # drive the bots from the phone, which nothing in the UI currently does.
    [switch]$KeepTradingEndpoints
)

$ErrorActionPreference = "Stop"
function Step { param([string]$m) Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Ok   { param([string]$m) Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn { param([string]$m) Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Info { param([string]$m) Write-Host "  $m" -ForegroundColor Gray }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this in an elevated PowerShell (Run as Administrator)."
}

# --- install ---------------------------------------------------------------
Step "Installing Tailscale"
if (Get-Command tailscale -ErrorAction SilentlyContinue) {
    Ok "Already installed"
} else {
    winget install --id Tailscale.Tailscale --source winget --silent `
        --accept-package-agreements --accept-source-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
        Warn "Installed, but not on PATH in this shell."
        Warn "Close this window, open a new elevated PowerShell, and re-run."
        exit 1
    }
    Ok "Installed"
}

# --- sign in ---------------------------------------------------------------
Step "Connecting to your tailnet"
$status = & tailscale status 2>&1 | Out-String
if ($status -match "Logged out" -or $LASTEXITCODE -ne 0) {
    Warn "Not signed in. A browser link will be printed - open it on any device"
    Warn "and sign in with the SAME account you will use on your phone."
    & tailscale up
}
Start-Sleep -Seconds 3

$tsIp = (& tailscale ip -4 2>$null | Select-Object -First 1)
if (-not $tsIp) {
    Warn "No Tailscale address yet. Run 'tailscale up', complete sign-in, then re-run."
    exit 1
}
Ok "Tailscale address: $tsIp"

# --- read-only -------------------------------------------------------------
Step "Closing the trading endpoints"
$envPath = Join-Path $RepoRoot "services\trader\.env"
$value = if ($KeepTradingEndpoints) { "false" } else { "true" }
if (Test-Path $envPath) {
    $lines = @(Get-Content $envPath | Where-Object { $_ -notmatch '^\s*API_READ_ONLY=' })
    $lines += "API_READ_ONLY=$value"
    Set-Content -Path $envPath -Value $lines -Encoding ascii
    if ($KeepTradingEndpoints) {
        Warn "API_READ_ONLY=false - operator endpoints stay reachable from your tailnet."
    } else {
        Ok "API_READ_ONLY=true - reads only; order submission is refused"
    }
} else {
    Warn "No .env at $envPath; run configure.ps1 first."
}

# --- rebind ----------------------------------------------------------------
# Binding the Tailscale address specifically, rather than 0.0.0.0, means the
# socket cannot be reached from the public interface at all - the protection
# is in the bind, not only in a firewall rule someone might later change.
Step "Rebinding the service to $tsIp"
$runner = Join-Path $RepoRoot "scripts\vps\run-bot.ps1"
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument ("-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" " +
               "-RepoRoot `"$RepoRoot`" -Port $Port -BindAddress $tsIp")
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal | Out-Null
Ok "Task updated to bind $tsIp"

# Allow the port on the Tailscale interface only.
Remove-NetFirewallRule -Name "vtfx-api-tailscale" -ErrorAction SilentlyContinue
New-NetFirewallRule -Name "vtfx-api-tailscale" -DisplayName "VTFX dashboard (Tailscale)" `
    -Enabled True -Direction Inbound -Protocol TCP -LocalPort $Port `
    -RemoteAddress 100.64.0.0/10 -Action Allow | Out-Null
Ok "Port $Port allowed from the tailnet range only"

Step "Restarting"
Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*run-bot*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 3
Start-ScheduledTask -TaskName $TaskName
Ok "Started"

Write-Host @"

=== On your phone ===

  1. Install Tailscale from the App Store or Play Store
  2. Sign in with the SAME account you just used
  3. Open:  http://${tsIp}:${Port}

  Add it to your home screen and it behaves like an app.

  Works from any network - mobile data, someone else's wifi - with nothing
  published. Only devices signed into your tailnet can reach it.

  The dashboard is read-only: it shows each bot's trades, win rate and P/L.
  Trading endpoints return 403 while API_READ_ONLY=true.

  Give it a minute after a VPS reboot: Tailscale reconnects, then MT5, then
  the bots.
"@ -ForegroundColor Cyan
