# Cross-venue arb (Polymarket + Kalshi, World Cup)

Price-only default stack comparing curated pairs on both venues. **Recommendations and paper sim only** — no live execution.

## Operator checklist

1. **Migrate:** `ploy-migrate`
2. **Polymarket:** set `POLY_GAMMA_TAGS` / `POLY_GAMMA_EVENT_SLUGS` for World Cup markets in `.env`
3. **Edit pairs:** [`config/cross_venue/world_cup_pairs.yaml`](../config/cross_venue/world_cup_pairs.yaml) — real `poly_market_id` + `kalshi_ticker`, set `active: true` after resolution rules match
4. **Load pairs:** `ploy-kalshi load-pairs config/cross_venue/world_cup_pairs.yaml`
5. **Start stack:** `ploy-ingest`, `ploy-kalshi-ingest`, `ploy-reason`, `ploy-notify`, `ploy-sim forward`, `ploy-web`
6. **Verify:** dashboard **Cross-venue** panel or `GET /api/cross-venue/spreads`
7. **Track:** `/paper` for sim P&L; tune `CROSS_VENUE_MIN_EDGE_CENTS` (default 8¢)

## Optional ESPN / game strategies

```bash
ENRICHMENT_ENABLED=true
AGENT_STRATEGIES=baseline_model,cross_venue_arb,cross_market_arb,book_imbalance
docker compose --profile sports up -d enrich
```

## Key env vars

| Var | Default |
|-----|---------|
| `KALSHI_ENABLED` | `true` |
| `KALSHI_BASE_URL` | `https://api.elections.kalshi.com/trade-api/v2` |
| `CROSS_VENUE_MIN_EDGE_CENTS` | `8` |
| `CROSS_VENUE_MAX_STALE_SEC` | `30` |
| `CROSS_VENUE_MIN_DEPTH` | `500` |
| `POLY_FEE_RATE` / `KALSHI_FEE_RATE` | `0.02` / `0.01` |

## Exit policy (paper sim)

Same as other strategies: resolution, reverse signal, max hold — see [paper-trading.md](paper-trading.md).
