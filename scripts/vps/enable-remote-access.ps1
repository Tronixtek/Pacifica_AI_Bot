<#
.SYNOPSIS
    Set up SSH so the dashboard can be reached over a tunnel. Run once.

.DESCRIPTION
    The trader API has NO authentication and can place trades:
    POST /api/operator/test-order opens a real position. Its only protection
    is that uvicorn binds 127.0.0.1 and refuses non-local connections.

    So the dashboard is never exposed directly. This installs OpenSSH Server
    and lets you forward the port over an authenticated, encrypted channel
    instead - the page stays on loopback at the VPS end.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File enable-remote-access.ps1 -AllowFromIp 203.0.113.7
#>
[CmdletBinding()]
param(
    # Restrict SSH to one address. Leave blank to allow any, which is worth
    # avoiding: port 22 on a public IP is found by scanners within hours.
    [string]$AllowFromIp = "",
    [int]$Port = 8011,
    [switch]$DisablePasswordAuth
)

$ErrorActionPreference = "Stop"
function Step { param([string]$m) Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Ok   { param([string]$m) Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn { param([string]$m) Write-Host "  [!!] $m" -ForegroundColor Yellow }

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this in an elevated PowerShell (Run as Administrator)."
}

Step "Installing OpenSSH Server"
$cap = Get-WindowsCapability -Online -Name OpenSSH.Server* | Select-Object -First 1
if ($cap.State -ne "Installed") {
    Add-WindowsCapability -Online -Name $cap.Name | Out-Null
    Ok "Installed $($cap.Name)"
} else {
    Ok "Already installed"
}

Step "Starting sshd"
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
Ok "sshd running and set to start on boot"

Step "Firewall"
Remove-NetFirewallRule -Name "vtfx-ssh" -ErrorAction SilentlyContinue
$rule = @{
    Name = "vtfx-ssh"; DisplayName = "OpenSSH (VTFX)"; Enabled = "True"
    Direction = "Inbound"; Protocol = "TCP"; Action = "Allow"; LocalPort = 22
}
if ($AllowFromIp) { $rule["RemoteAddress"] = $AllowFromIp }
New-NetFirewallRule @rule | Out-Null
if ($AllowFromIp) {
    Ok "Port 22 allowed from $AllowFromIp only"
} else {
    Warn "Port 22 allowed from ANY address."
    Warn "Re-run with -AllowFromIp <your.ip> to narrow it."
}

# The trading port must never be reachable from outside. An explicit block
# rule guards against a later change to the security group or the bind address.
Step "Blocking the trading port at the edge"
Remove-NetFirewallRule -Name "vtfx-api-block" -ErrorAction SilentlyContinue
New-NetFirewallRule -Name "vtfx-api-block" -DisplayName "Block VTFX API from network" `
    -Enabled True -Direction Inbound -Protocol TCP -LocalPort $Port -Action Block | Out-Null
Ok "Inbound $Port blocked; reachable only through the tunnel"

if ($DisablePasswordAuth) {
    Step "Disabling password authentication"
    $cfg = "$env:ProgramData\ssh\sshd_config"
    if (Test-Path $cfg) {
        $keys = "$env:ProgramData\ssh\administrators_authorized_keys"
        if (-not (Test-Path $keys)) {
            Warn "No $keys yet - install your public key BEFORE relying on this,"
            Warn "or you will lock yourself out of SSH."
        } else {
            (Get-Content $cfg) -replace '^#?PasswordAuthentication.*','PasswordAuthentication no' |
                Set-Content $cfg -Encoding utf8
            Restart-Service sshd
            Ok "Password auth off; key auth only"
        }
    }
}

$ip = (Invoke-RestMethod -Uri "https://api.ipify.org?format=json" -TimeoutSec 8 -ErrorAction SilentlyContinue).ip
$who = $env:USERNAME

Write-Host @"

=== Done ===

  From YOUR machine, open the tunnel:

      ssh -N -L ${Port}:127.0.0.1:${Port} ${who}@$(if($ip){$ip}else{'<vps-public-ip>'})

  Leave it running, then browse to:

      http://127.0.0.1:${Port}

  What this gives you:
    - the dashboard and API stay bound to loopback on the VPS
    - the only open port is 22, authenticated and encrypted
    - closing the tunnel closes all access

  Still to do:
    - allow port 22 from your IP only in the AWS security group
    - keep uvicorn on --host 127.0.0.1 in run-bot.ps1; that flag is the
      security boundary, not a preference

  Do NOT publish port $Port. The API has no authentication and
  POST /api/operator/test-order places a real trade.
"@ -ForegroundColor Cyan
