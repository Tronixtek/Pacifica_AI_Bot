# Architecture

## Goal

Build a Pacifica-native trading application that spots price-action setups, executes with risk controls, and explains itself through a live dashboard.

## System shape

- `apps/web`: dashboard and operator UX
- `services/trader`: market data, strategy, risk, execution, and API
- `packages/shared`: frontend-facing contracts for the dashboard

## Current backend status

- paper-trading loop is running conceptually and verified for syntax
- Pacifica market specs and prices are wired through a dedicated adapter
- websocket subscriptions are prepared for `prices` and per-symbol `bbo`
- signed execution payloads now include API Agent Key support and `builder_code`
- remote account sync now polls Pacifica account info, positions, and open orders
- runtime state tracks `last_order_id` from Pacifica account position/order endpoints
- diagnostics endpoint is available for config, dependency, cache, and live REST readiness checks
- operator endpoints now support pause/resume, forced account sync, and execution payload preview for existing signals

## Pacifica mapping

- Prices: `GET /api/v1/info/prices`
- Market info: `GET /api/v1/info`
- Candles: `GET /api/v1/kline`
- Account info: `GET /api/v1/account`
- Positions: `GET /api/v1/positions`
- Open orders: `GET /api/v1/orders`
- Orderbook: `GET /api/v1/book`
- Market orders: `POST /api/v1/orders/create_market`
- Position TP/SL: `POST /api/v1/positions/tpsl`
- WebSocket prices: `{"method":"subscribe","params":{"source":"prices"}}`
- WebSocket BBO: `{"method":"subscribe","params":{"source":"bbo","symbol":"BTC"}}`
- Event ordering: use `last_order_id`
- Safer automation: use API Agent Keys / agent wallets

## Next slices

1. Validate testnet execution end-to-end with a configured Pacifica account and agent key
2. Add guarded live order submission from the operator console after credential testing
3. Add account websocket sync to reduce polling latency and keep `last_order_id` aligned in real time
4. Build wallet-connect and builder-code approval flow in the dashboard
