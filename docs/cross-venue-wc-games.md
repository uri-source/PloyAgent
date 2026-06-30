# Cross-venue World Cup **game** moneylines

Auto-map Polymarket `fifwc-*` match markets to Kalshi `KXWCGAME` tickers for cross-venue arb + consensus sim.

## Prerequisites

1. `ploy-migrate` (includes `008_cross_venue_match_meta.sql`)
2. `ploy-ingest` with WC discovery:
   ```bash
   POLY_GAMMA_SERIES_SLUGS=soccer-fifwc
   POLY_GAMMA_TAGS=soccer,fifa-world-cup
   POLY_GAMMA_DISCOVERY_LIMIT=500
   ```
   Game moneylines use Gamma series **`soccer-fifwc`** (`fifwc-*` event slugs). Tag-only `soccer` misses most WC games.
3. `KALSHI_ENABLED=true`

## Map pairs

```bash
# Preview
ploy-kalshi map-wc-games --dry-run

# Write to cross_venue_pairs (high confidence → active=true)
ploy-kalshi map-wc-games
```

Re-run every 6h (cron) as new games list on both venues.

## Strategy stack

```bash
AGENT_STRATEGIES=book_imbalance,cross_venue_arb,consensus
```

- **consensus must be last** — fires only when 2+ strategies agree on direction
- Sim profile **`e8_c70_m65`** trades **consensus only** (`SIM_FORWARD_PROFILES=e8_c70_m65`)

## Verify

```bash
curl -s http://127.0.0.1:8765/api/cross-venue/spreads | python3 -m json.tool
docker compose logs --tail=20 kalshi-ingest
```

Low-confidence pairs are stored with `active=false` and `match_source=auto_wc_game`.

See also [cross-venue-world-cup.md](cross-venue-world-cup.md) for tournament-winner YAML pairs.
