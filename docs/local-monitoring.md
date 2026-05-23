# Run and monitor locally (outside Cursor)

## Quick start

```bash
cd /path/to/PloyAgent
docker compose -f infra/docker-compose.yml up -d   # TimescaleDB on :5433
source .venv/bin/activate
ploy-migrate
ploy-sim init-profiles   # once

./scripts/run-local-stack.sh
```

Open **http://127.0.0.1:8765** in a browser. Use Cursor only to read logs or code — processes keep running in Terminal/iTerm.

## Stop everything

```bash
./scripts/stop-local-stack.sh
```

## Monitor

| What | Command |
|------|---------|
| Ingest / prices | `tail -f artifacts/ingest.log` |
| Simulation | `tail -f artifacts/sim-forward.log` |
| Reasoning | `tail -f artifacts/reason.log` |
| All PIDs | `pgrep -fl ploy-` |
| DB: price freshness | `psql $DATABASE_URL -c "SELECT MAX(ts) FROM prices"` |
| Sim summary | `ploy-sim compare` |

## Simulation duration (defined defaults)

| Mode | What runs | Default length | Override |
|------|-----------|----------------|----------|
| **Forward** | `ploy-sim forward` (in `run-local-stack.sh`) | **14 days** (`SIM_FORWARD_RUN_HOURS=336`), then exits cleanly | `SIM_FORWARD_RUN_HOURS=0` for unlimited; `ploy-sim forward --hours 48` for 2-day smoke test |
| **Replay** | `ploy-sim replay` (manual / cron) | **14 days** of `fair_values` | `SIM_REPLAY_DAYS=7` or `ploy-sim replay --days 7` |

**When results are “good enough”** (in addition to the time window):

| Goal | Forward | Replay |
|------|---------|--------|
| Trades showing up | ≥24h | any |
| Profile ranking | **≥50 closed trades** on best profile | **≥30 closed/profile** (smoke), **≥100** (reliable) |
| P&L chart useful | **≥20 closed trades** on one profile | same |

IPO-heavy books resolve slowly — rely more on **replay**; keep forward running for the full 14 days or set `SIM_FORWARD_RUN_HOURS=672` (28 days).

Check progress on the dashboard **Simulation** section or:

```bash
ploy-sim compare
ploy-sim report --profile e5_c65_m60 --by market
```

**Chart readiness:** cumulative P&L chart is useful after **~20+ closed trades** on a profile.

## macOS TLS

If ingest logs show `CERTIFICATE_VERIFY_FAILED`, set in `.env`:

```env
PLOY_INSECURE_SSL=true
```

Then restart ingest (`./scripts/stop-local-stack.sh` && `./scripts/run-local-stack.sh`).

## ISP / geo block (Israel, US, etc.)

If logs show redirects to a carrier filter page (e.g. `filtering.bezeq.co.il`) when calling `clob.polymarket.com`, your network is blocking Polymarket REST. **Use a VPN or VPS in an allowed region** (see `infra/README.md`). WebSocket ticks may still fail until egress is fixed — `prices` will stay stale without a working path.
