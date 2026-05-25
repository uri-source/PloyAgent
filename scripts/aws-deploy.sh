#!/usr/bin/env bash
# Deploy PloyAgent on AWS EC2 (Docker Compose + prod + AWS overlays). Run ON THE INSTANCE.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.aws.yml)

if [[ ! -f .env ]]; then
  echo "Missing .env — use scripts/aws-ssm-pull-env.sh or cp .env.production.example .env"
  exit 1
fi

if ! grep -q '^POSTGRES_PASSWORD=.' .env || grep -q 'change-me' .env; then
  echo "Set a strong POSTGRES_PASSWORD and matching DATABASE_URL in .env"
  exit 1
fi

echo "==> Building and starting stack (production + AWS overlays)..."
"${COMPOSE[@]}" build
"${COMPOSE[@]}" up -d

echo "==> Waiting for web health..."
for _ in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8765/healthz >/dev/null; then
    echo "OK: http://127.0.0.1:8765/healthz"
    break
  fi
  sleep 2
done

echo ""
echo "Post-deploy (see docs/aws-hosting.md):"
echo "  ${COMPOSE[*]} exec web ploy-migrate"
echo "  ${COMPOSE[*]} run --rm web python scripts/backfill_market_type.py"
echo "  ${COMPOSE[*]} run --rm web ploy-sim init-profiles   # optional"
