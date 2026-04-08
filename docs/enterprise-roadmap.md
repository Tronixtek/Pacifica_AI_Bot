# Enterprise Roadmap

## Current truth

The project is now beyond a simple mockup, but it is not yet an enterprise trading platform. It has:

- live Pacifica integration
- local ML training and persisted artifacts
- operator controls and diagnostics
- structured request logging
- persistent audit logging for critical operator actions
- liveness and readiness endpoints
- SQLite-backed durable runtime state with restart recovery for paper balance, positions, signals, events, and session-linked account context

## Biggest gaps to close

1. Multi-tenant identity and RBAC
2. Normalized durable trading ledger instead of single-snapshot runtime persistence
3. Job orchestration for trading, retraining, and data pipelines
4. Secret management and environment isolation
5. Real monitoring, alerting, and metrics
6. Deployment topology with worker separation and failover
7. Compliance, audit review, and approval workflows

## Recommended build order

### Phase 1: Platform foundation

- request tracing and structured logs
- persistent audit trail
- liveness/readiness endpoints
- artifact and dataset persistence
- durable runtime state checkpoints and restart recovery

### Phase 2: Data and model platform

- dedicated feature store
- scheduled retraining jobs
- offline backtests and evaluation reports
- model registry and promotion workflow

### Phase 3: Trading control plane

- idempotent execution workflows
- circuit breakers and kill switches
- reconciliation jobs for Pacifica account state
- durable order, position, and fill ledger

### Phase 4: Productization

- user accounts, teams, workspaces
- RBAC and approval flows
- billing and plan enforcement
- tenant-level strategy and risk policies

### Phase 5: Production operations

- metrics, dashboards, alerts
- worker autoscaling
- blue/green deployment and rollback
- disaster recovery and incident playbooks

## Immediate next engineering slice

Replace snapshot persistence with a normalized execution ledger and reconciliation pipeline so orders, fills, positions, and strategy decisions have full history and deterministic replay.
