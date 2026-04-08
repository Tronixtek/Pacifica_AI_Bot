# Pacifica Price Action Bot

An AI-assisted, price-action trading bot for Pacifica built as a hackathon project. The scaffold is designed to be testnet-first for safe validation and mainnet-capable after execution hardening.

## Project layout

- `apps/web`: Next.js dashboard for signals, positions, PnL, and operator controls
- `services/trader`: FastAPI service with strategy, risk, and Pacifica integration hooks
- `packages/shared`: shared TypeScript dashboard contracts
- `docs/architecture.md`: implementation roadmap and module responsibilities

## Render deployment

This repo is now set up for a two-service Render deployment with the root [render.yaml](C:/Users/PC/Desktop/pacifica_hakerthon/render.yaml):

- `pacifica-ai-bot-web`: Next.js frontend web service
- `pacifica-ai-bot-trader`: FastAPI trader web service

Why two services:

- the frontend is a Next.js app that should run as a Node web service
- the trader is an always-on FastAPI bot with websocket market data and background loops
- the trader also needs persistent storage for runtime state, logs, and ML artifacts

The Render blueprint is configured to:

- run the frontend from the repo root so npm workspaces can include `packages/shared`
- run the trader from `services/trader`
- attach a persistent disk to the trader at `/var/data/pacifica-trader`
- keep the initial cloud deploy in safe testnet observation mode with `ENABLE_LIVE_TRADING=false`

Before you enable signed testnet execution on Render, set these trader secrets in the Render dashboard:

- `PACIFICA_ACCOUNT_ADDRESS`
- `PACIFICA_AGENT_PRIVATE_KEY`
- optionally `PACIFICA_API_CONFIG_KEY`

If you keep the service names from the blueprint, these public URLs are expected:

- frontend: `https://pacifica-ai-bot-web.onrender.com`
- backend: `https://pacifica-ai-bot-trader.onrender.com`

If you rename either service in Render, update:

- frontend `NEXT_PUBLIC_TRADER_API_URL`
- backend `FRONTEND_ORIGIN`

## Immediate milestones

1. Start with paper trading on a simulated feed.
2. Stream or poll Pacifica market data with REST fallback.
3. Build signed testnet/mainnet-ready market-order payloads with builder-code support.
4. Finish wallet flow, builder-code approval, and live account syncing in the dashboard.

## Testnet phase

The local trader service is now configured for a safe testnet phase:

- `services/trader/.env` sets `BOT_MODE=testnet`
- live Pacifica testnet market data is enabled with `USE_SIMULATED_FEED=false`
- signed order submission stays off with `ENABLE_LIVE_TRADING=false`
- daily loss protection is enabled again for this phase

That means the bot will use real Pacifica testnet prices and account sync logic, but it will not submit testnet orders until you add credentials and intentionally enable them.

To finish the move into signed testnet execution later:

1. Add your Pacifica testnet account to `PACIFICA_ACCOUNT_ADDRESS`
2. Add your Pacifica agent key to `PACIFICA_AGENT_PRIVATE_KEY`
3. Set `ENABLE_LIVE_TRADING=true`
4. Restart the backend

Recommended restart flow:

```powershell
cd C:\Users\PC\Desktop\pacifica_hakerthon\services\trader
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

If you want the trader service on port `8011` instead of `8000`, start it like this:

```powershell
cd C:\Users\PC\Desktop\pacifica_hakerthon\services\trader
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8011
```

## Current status

- The backend exposes `GET /health`, `GET /api/overview`, and `GET /api/diagnostics`.
- The overview snapshot now includes:
  - paper/runtime account state
  - synced Pacifica account balances and fee settings when an account is configured
  - mirrored Pacifica positions
  - mirrored Pacifica open orders
  - latest `last_order_id` captured from account position/order queries
- The dashboard now includes operator controls for:
  - pausing and resuming strategy scanning
  - forcing Pacifica account sync
  - previewing order payloads from generated signals
  - filtering and navigating the console in real time
- The dashboard renders separate panels for:
  - engine positions
  - Pacifica positions
  - Pacifica open orders
  - account-level sync metadata

## Diagnostics

The backend now exposes a diagnostics endpoint:

- `GET /api/diagnostics`
- `GET /api/diagnostics?live_probe=true`

Use `live_probe=true` to verify public Pacifica REST connectivity and overall runtime readiness without placing any trades.

For local paper-mode development, the daily loss stop is now disabled by default so the strategy can keep firing while we test the product flow. Re-enable it with `ENFORCE_DAILY_LOSS_LIMIT=true` when you want strict protection again.

## ML training data

The trader service now includes a Pacifica-native data collector so we can train the ML layer on real exchange data instead of only in-memory paper history.

Backfill historical mark candles plus a recent-trades snapshot:

```powershell
cd C:\Users\PC\Desktop\pacifica_hakerthon\services\trader
.\.venv\Scripts\Activate.ps1
python -m app.training backfill --symbols BTC,ETH,SOL --intervals 1m,5m,15m --lookback-days 30
```

Append live websocket prices and trades for future retraining:

```powershell
cd C:\Users\PC\Desktop\pacifica_hakerthon\services\trader
.\.venv\Scripts\Activate.ps1
python -m app.training stream --symbols BTC,ETH,SOL
```

Fit the ML model from the collected dataset and persist the artifact:

```powershell
cd C:\Users\PC\Desktop\pacifica_hakerthon\services\trader
.\.venv\Scripts\Activate.ps1
python -m app.training fit --symbols BTC,ETH,SOL
```

Collected data is written under `services/trader/data/training/` with:

- `raw/<network>/<symbol>/<interval>/mark_candles.jsonl`
- `raw/<network>/<symbol>/recent_trades.jsonl`
- `stream/<network>/<symbol>/prices.jsonl`
- `stream/<network>/<symbol>/trades.jsonl`
- `manifest.json`

The ML model now prefers this local dataset automatically at runtime. If local candle files are missing or too short, it falls back to Pacifica REST candle fetches. Successful training also saves a reusable artifact under `services/trader/models/ml_signal_model.json`, and the bot loads that artifact on startup before attempting a fresh retrain.

## Enterprise foundation

The service now includes the first real platform-hardening slice:

- structured application logs
- request IDs on API responses
- `GET /livez` and `GET /readyz`
- persistent audit logs at `services/trader/logs/audit.jsonl`
- SQLite-backed runtime state checkpoints at `services/trader/data/state/runtime.sqlite3`
- automatic recovery of paper balance, open paper positions, signals, events, and session-linked Pacifica account state after restart

See `docs/enterprise-roadmap.md` for the broader production roadmap.
