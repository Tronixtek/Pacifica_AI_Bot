# Deploying to a Windows VPS

The bot must run on **Windows**, on the **same machine** as a running MetaTrader 5
terminal. The `MetaTrader5` Python package has no Linux build and no remote mode —
it talks to the terminal over local IPC. There is no way around this.

## Sizing

Measured working set on a live install:

| process | RAM |
|---|---|
| `terminal64.exe` (MT5, no charts open) | ~35–300 MB |
| `python.exe` (the bot) | ~70 MB |
| Windows Server itself | ~800 MB–1 GB |

**2 GB is the practical minimum** (AWS `t3.small`). The 1 GB free-tier sizes
(`t2.micro` / `t3.micro`) will run but swap heavily.

## Order of operations

The scripted part comes first, but MT5 needs a GUI and your credentials, so it
stays manual.

### 1. Run the setup script

In an **elevated** PowerShell on the VPS:

```powershell
cd C:\
git clone --branch mt5-forex-hardening https://github.com/Tronixtek/Pacifica_AI_Bot.git temp-setup
powershell -ExecutionPolicy Bypass -File C:\temp-setup\scripts\vps\setup.ps1 `
    -Branch mt5-forex-hardening `
    -EnableAutoLogon -AutoLogonUser "Administrator" -AutoLogonPassword "<your-password>"
```

That installs Python and Git, clones to `C:\vtfx\Pacifica_AI_Bot`, builds the
virtualenv, and registers a scheduled task.

### 2. Install and configure MetaTrader 5

Download your broker's terminal (for Exness, from your Personal Area) and install it.
Then:

- **Log in and tick "save password"** — without it the terminal starts logged out
  after a reboot and the bot has nothing to talk to.
- **Tools → Options → Expert Advisors → "Allow algorithmic trading"**. Use this,
  not the toolbar button: the toolbar toggle resets on every restart, and every
  order is rejected with retcode `10027` until it is on.
- **Close all charts** and trim Market Watch to just the symbols you trade. The bot
  uses the Python API and never needs a chart; this is most of the memory saving.

### 3. Configure the bot

Edit `C:\vtfx\Pacifica_AI_Bot\services\trader\.env`:

```
BOT_MODE=demo
MT5_LOGIN=<your login>
MT5_SERVER=<your server>
SYMBOLS=EURUSD,GBPUSD,BTCUSD
ENABLE_LIVE_TRADING=true
```

`MT5_LOGIN` is a **guard**, not a credential — the bot refuses to start if the
terminal is logged into a different account. Keep it accurate, especially if you
ever point this at a live account.

### 4. Start it

```powershell
Start-ScheduledTask -TaskName VTFX-Bot
curl http://127.0.0.1:8011/health
```

## Everyday use

Once set up, one command does everything:

```powershell
powershell -ExecutionPolicy Bypass -File C:\vtfx\Pacifica_AI_Bot\scripts\vps\update.ps1
```

It stops the bots, pulls the branch, reinstalls dependencies **only if
`requirements.txt` changed**, warns about any new settings your `.env` does not
set, restarts, waits for health, and prints each bot's performance.

It **refuses to restart while positions are open** unless you pass `-Force`.
Broker-side stops survive a restart, but trailing state lives in memory — the
position would ride to its original stop with nothing managing it.

## Reaching the dashboard from your own machine

The API has **no authentication** and can place trades — `POST
/api/operator/test-order` opens a real position. Its only protection is that
uvicorn binds `127.0.0.1`. **Never publish port 8011.**

Run once on the VPS:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\vps\enable-remote-access.ps1 -AllowFromIp <your.ip>
```

That installs OpenSSH Server, restricts port 22 to your address, and adds an
explicit firewall block on 8011 so a later change to the security group or the
bind address cannot expose it.

Then from your machine:

```powershell
ssh -N -L 8011:127.0.0.1:8011 Administrator@<vps-ip>
```

Leave it running and open `http://127.0.0.1:8011`. Closing the tunnel closes all
access.

FastAPI serves the dashboard itself, so there is no Node process and no second
port — `apps/web/out` is committed already built.

## Viewing from your phone, from anywhere

```powershell
powershell -ExecutionPolicy Bypass -File scriptsps\enable-phone-access.ps1
```

Installs Tailscale, signs the VPS into your tailnet, rebinds the service to
the private Tailscale address, and sets `API_READ_ONLY=true`.

Then install Tailscale on your phone, sign in with the same account, and open
`http://<tailscale-ip>:8011`.

Nothing is published. Only devices signed into your tailnet can reach the
service, and the port is bound to the Tailscale address specifically rather
than `0.0.0.0` - so the protection is in the socket, not only in a firewall
rule someone might later change.

`API_READ_ONLY=true` refuses every operator endpoint. The dashboard only
reads, and a mistyped URL or a stale browser tab should not be able to open a
position.

## Surviving reboots

A VPS reboots — for updates, for host maintenance, or because AWS says so. Three
things have to line up or the bot silently stops trading:

| requirement | why | set by |
|---|---|---|
| auto-logon | MT5 is a desktop app and needs an interactive session | `setup.ps1 -EnableAutoLogon` |
| MT5 auto-login | terminal must reconnect to the broker unattended | "save password" at login |
| algo trading persisted | the toolbar toggle resets on restart | Tools → Options |

The scheduled task runs **at logon**, not at startup, precisely so it lands in the
same session as MT5.

**Disconnect from RDP, never log off.** Logging off ends the session and kills the
terminal. Closing the RDP window leaves it running.

## Checking on it

```powershell
curl http://127.0.0.1:8011/health
Get-Content C:\vtfx\Pacifica_AI_Bot\logs\supervisor.log -Tail 30
Get-Content C:\vtfx\Pacifica_AI_Bot\logs\backend.log -Tail 50
Get-Content C:\vtfx\Pacifica_AI_Bot\services\trader\logs\audit.jsonl -Tail 20
```

`supervisor.log` records MT5 readiness and every backend restart. `audit.jsonl` is
the durable record of orders, trailed stops and operator actions.

To see the dashboard from your own machine, tunnel over RDP or SSH rather than
opening the port. The backend binds to `127.0.0.1` deliberately — it has **no
authentication**, and it can place trades.

## Costs worth knowing

- `t3.small` running Windows 24/7 is roughly **$15–20/month**. It is not free tier.
- Set an AWS billing alarm before you walk away from it.
- If you fund a live account, check whether your broker offers a free VPS — it sits
  next to their trade servers and costs nothing.

## Security

- Restrict RDP (port 3389) to your own IP in the security group. Never leave it open
  to the world.
- `-EnableAutoLogon` stores the password in plaintext in the registry. That is the
  trade for unattended restarts; treat the box as compromised-if-accessed.
- The bot API has no auth. Keep it on `127.0.0.1`.

## Troubleshooting

**`The argument ... to the -File parameter does not exist`**
The clone fetched the default branch, which does not contain `scripts/vps`.
Clone with `--branch mt5-forex-hardening`.

**`Python was not found; run without arguments to install from the Microsoft Store`**
`python` on PATH is the Windows App Execution Alias stub, not an interpreter.
The setup script detects and skips it, but if you hit this running Python by
hand, use the `py` launcher or the full path under
`%LOCALAPPDATA%\Programs\Python`.

**`0x8a15005e : The server certificate did not match any of the expected values`**
winget probed the `msstore` source, which is not usable on Windows Server.
Pin the source:

```powershell
winget install --id Python.Python.3.12 --source winget --silent `
    --accept-package-agreements --accept-source-agreements
```

**A tool installs but is still "not found"**
PATH is read once when a shell starts. Close the window, open a new elevated
PowerShell, and re-run.

**`retcode 10027` on every order**
Algorithmic trading is off in the terminal. Enable it in
Tools > Options > Expert Advisors, not the toolbar button - the toolbar toggle
resets on restart.

## Restarting after a config change

Use `stop-bot.ps1`, not `Stop-ScheduledTask` on its own.

```powershell
powershell -ExecutionPolicy Bypass -File scriptsps\stop-bot.ps1 -ThenStart
```

`Stop-ScheduledTask` stops the supervisor but leaves the python backend it
spawned still running and still holding port 8011. The next start then hits
the supervisor's own port guard and exits, which is correct - two instances
would trade one account under one magic number - but it fails silently: the
API still answers, the dashboard still renders, and the OLD configuration
keeps running. A config change appears to deploy and does not.

The tell is the startup line. Check the timestamp is fresh and the equity
matches the account:

```powershell
Select-String -Path logsackend.log -Pattern '\[edge\]' | Select-Object -Last 6
```
