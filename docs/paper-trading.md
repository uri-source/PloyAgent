# Paper trading performance

PloyAgent tracks **simulated** BUY/SELL decisions in `sim_trades` — not Slack and not live Polymarket orders. Use this for performance over time and exit-rule evaluation.

**Run 24/7 without your laptop:** [simple-vps-guide.md](simple-vps-guide.md).

## Run paper trading

```bash
ploy-migrate
ploy-sim init-profiles
ploy-sim forward   # live paper trades on current forward run
```

Keep `ploy-ingest`, `ploy-reason`, and `ploy-notify` running so `top_picks` feeds the simulator.

Default forward run length: `SIM_FORWARD_RUN_HOURS` (14 days).

## View performance

| Surface | URL |
|---------|-----|
| **Paper trading page** | http://127.0.0.1:8765/paper |
| Dashboard sim section | http://127.0.0.1:8765/ (scroll to Simulation) |
| Performance API | `GET /api/sim/performance?profile_id=e5_c65_m60` |
| Decision ledger API | `GET /api/sim/trades?limit=500&status=closed` |
| Daily rollup (in performance payload) | `performance.daily[]` |

Recommendations analytics (`/analytics`) tracks **approved recs held to resolution** — different model. For trading-style exits, use paper trading only.

## Exit criteria (implemented)

| `close_reason` | When |
|----------------|------|
| `resolution` | Market settled; binary P&L from outcome |
| `signal_reverse` | Edge flips direction and passes profile entry gates; MTM exit |
| `max_hold` | Held past category max (sports: 24h; default: 7d); MTM exit |
| `mark_to_market` | End of replay/forward run bulk close |

Code: `src/ploy_agent/sim/portfolio.py`.

## Entry criteria (per profile)

Profiles are named like `e5_c65_m60` = min **5¢** edge, **65%** confidence, **60%** model probability (directional for SELL). Grid: `ploy-sim init-profiles`.

Phase 1 guardrails (entry price band, min risk/reward) also apply in sim — same as live ranking.

## APIs for automation (e.g. future Slack daily)

```bash
curl -s http://127.0.0.1:8765/api/sim/performance | jq '.totals, .daily[-1], .by_close_reason'
```

`daily[-1]` is the most recent day with activity. Aggregate all profiles by omitting `profile_id`, or filter one profile.

## SQL export

```sql
SELECT profile_id, direction, question, opened_at, closed_at,
       entry_price, exit_price, close_reason, pnl_cents, status
FROM sim_trades
WHERE sim_run_id = (
  SELECT id FROM sim_runs WHERE mode = 'forward'
  ORDER BY ended_at NULLS FIRST, started_at DESC LIMIT 1
)
ORDER BY opened_at DESC;
```

## Slack daily updates (later)

Not required for tracking. When added, post a summary from `/api/sim/performance`:

- `totals.total_pnl_cents`, `totals.win_rate`
- Last row of `daily` (today’s closed count and P&L)
- Top `by_close_reason` rows

No Slack scopes needed until that notifier is built.
