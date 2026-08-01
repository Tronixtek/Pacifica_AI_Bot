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
git clone https://github.com/Tronixtek/Pacifica_AI_Bot.git temp-setup
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
