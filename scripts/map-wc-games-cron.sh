#!/usr/bin/env bash
# Cron helper: refresh Polymarket ↔ Kalshi WC game moneyline pairs.
# Install on VPS (every 6h):
#   0 */6 * * * /root/PloyAgent/scripts/map-wc-games-cron.sh >> /var/log/ploy-map-wc-games.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== $(date -u +"%Y-%m-%dT%H:%M:%SZ") map-wc-games ==="

OUT=$(docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm kalshi-ingest \
  ploy-kalshi map-wc-games 2>&1)
echo "$OUT"

# Surface new active pairs in cron log (e.g. for manual review or future alerting).
if echo "$OUT" | grep -qE 'active=[1-9]'; then
  echo "NOTICE: new active WC game pairs mapped — check /api/cross-venue/spreads"
fi
