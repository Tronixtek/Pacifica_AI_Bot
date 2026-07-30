# VTFX MT5 Bot

An AI-assisted, price-action trading bot for MetaTrader 5, adapted from an earlier Pacifica-exchange
scaffold. The scaffold is designed to be paper/demo-first for safe validation and live-capable after
execution hardening.

## Project layout

- `apps/web`: Next.js dashboard for signals, positions, PnL, and operator controls
- `services/trader`: FastAPI service with strategy, risk, and MT5 integration hooks
- `packages/shared`: shared TypeScript dashboard contracts
- `docs/architecture.md`: implementation roadmap and module responsibilities

## Hard platform constraint

The `MetaTrader5` Python package only works on **Windows**, and only when a MetaTrader 5 terminal is
installed, logged in, and running on the same machine as the backend process. There is no macOS or
Linux build. This means:

- `paper` mode (simulated feed, no MT5 connection) runs anywhere.
- `demo` and `live` modes only work on Windows, next to a running MT5 terminal.

## Running locally (Windows, for demo/live)

```powershell
cd services\trader
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env
```

Edit `services/trader/.env`:

- `BOT_MODE=demo` (or `paper` to skip MT5 entirely)
- `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` — your MT5 account credentials
- `MT5_TERMINAL_PATH` — optional, only needed if the terminal isn't auto-discovered
- `USE_SIMULATED_FEED=false` once you want real MT5 prices
- `ENABLE_LIVE_TRADING=false` until you've verified the full flow

Start the terminal in the background (log in once, manually), then start the backend:

```powershell
uvicorn app.main:app --reload
```

If you want the trader service on a different port:

```powershell
uvicorn app.main:app --reload --port 8011
```

## Running the dashboard

```powershell
cd apps\web
npm install
npm run dev
```

Or from the repo root, `npm run dev` starts both the backend (if a `.venv` exists under
`services/trader`) and the frontend together via `scripts/dev.js`.

## Paper mode (works on any OS)

Paper mode never touches MT5 at all — it's a fully simulated feed and account, useful for testing the
strategy, ML layer, risk manager, and dashboard without Windows or a broker account:

```bash
cd services/trader
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\Activate.ps1 on Windows
pip install -r requirements.txt
BOT_MODE=paper USE_SIMULATED_FEED=true uvicorn app.main:app --reload
```

## Demo/live phase

The trader service ships configured for a safe demo phase:

- `BOT_MODE=demo` connects to your MT5 terminal and syncs real account/position/order state
- `USE_SIMULATED_FEED=false` pulls live MT5 ticks
- `ENABLE_LIVE_TRADING=false` keeps order submission off until you're ready
- daily loss protection can be re-enabled with `ENFORCE_DAILY_LOSS_LIMIT=true`

To move into signed demo execution:

1. Set `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` in `.env`
2. Log into the MT5 terminal on the same machine (or let the backend log in for you)
3. Set `ENABLE_LIVE_TRADING=true`
4. Restart the backend

## Current status

- The backend exposes `GET /health`, `GET /api/overview`, and `GET /api/diagnostics`.
- The overview snapshot includes:
  - paper/runtime account state
  - synced MT5 account balances, leverage, and margin level when an account is configured
  - mirrored MT5 positions (with attached stop-loss/take-profit, since MT5 carries these on the
    position itself)
  - mirrored MT5 pending orders
- The dashboard includes operator controls for:
  - pausing and resuming strategy scanning
  - forcing MT5 account sync
  - previewing order payloads from generated signals
  - filtering and navigating the console in real time
- The dashboard renders separate panels for:
  - engine positions
  - MT5 positions
  - MT5 pending orders
  - account-level sync metadata

## Diagnostics

The backend exposes a diagnostics endpoint:

- `GET /api/diagnostics`
- `GET /api/diagnostics?live_probe=true`

Use `live_probe=true` to verify the MT5 terminal connection and overall runtime readiness without
placing any trades.

For local paper-mode development, the daily loss stop is disabled by default so the strategy can keep
firing while you test the product flow. Re-enable it with `ENFORCE_DAILY_LOSS_LIMIT=true` when you
want strict protection again.

## ML training data

The trader service includes an MT5-native data collector so the ML layer can train on real terminal
candle/tick history instead of only in-memory paper history.

Backfill historical mark candles plus a recent-ticks snapshot:

```powershell
cd services\trader
.\.venv\Scripts\Activate.ps1
python -m app.training backfill --symbols EURUSD,XAUUSD,BTCUSD --intervals 1m,5m,15m --lookback-days 30
```

Poll and append live ticks for future retraining:

```powershell
cd services\trader
.\.venv\Scripts\Activate.ps1
python -m app.training stream --symbols EURUSD,XAUUSD,BTCUSD
```

Fit the ML model from the collected dataset and persist the artifact:

```powershell
cd services\trader
.\.venv\Scripts\Activate.ps1
python -m app.training fit --symbols EURUSD,XAUUSD,BTCUSD
```

Collected data is written under `services/trader/data/training/` with:

- `raw/<mode>/<symbol>/<interval>/mark_candles.jsonl`
- `raw/<mode>/<symbol>/recent_ticks.jsonl`
- `stream/<mode>/<symbol>/ticks.jsonl`
- `manifest.json`

The ML model prefers this local dataset automatically at runtime. If local candle files are missing or
too short, it falls back to live MT5 candle fetches. Successful training also saves a reusable artifact
under `services/trader/models/ml_signal_model.json`, and the bot loads that artifact on startup before
attempting a fresh retrain.

## Cloud deployment

`render.yaml` deploys a **paper-mode-only** public demo (dashboard + backend), since MT5 execution can
only run on Windows next to a live terminal. Demo/live execution should be run locally on your own
Windows machine, not on Render.

## Enterprise foundation

The service includes a platform-hardening slice:

- structured application logs
- request IDs on API responses
- `GET /livez` and `GET /readyz`
- persistent audit logs at `services/trader/logs/audit.jsonl`
- SQLite-backed runtime state checkpoints at `services/trader/data/state/runtime.sqlite3`
- automatic recovery of paper balance, open paper positions, signals, events, and MT5 account state
  after restart

See `docs/enterprise-roadmap.md` for the broader production roadmap.
