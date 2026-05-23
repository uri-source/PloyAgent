#!/usr/bin/env bash
# First-time VPS deploy helper (Ubuntu). Run ON THE VPS after cloning the repo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "Missing .env — copy .env.production.example and edit secrets:"
  echo "  cp .env.production.example .env"
  exit 1
fi

if ! grep -q '^POSTGRES_PASSWORD=.' .env || grep -q 'change-me' .env; then
  echo "Set a strong POSTGRES_PASSWORD and matching DATABASE_URL in .env"
  exit 1
fi

echo "==> Building and starting stack (production overlay)..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml build
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

echo "==> Waiting for web health..."
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8765/healthz >/dev/null; then
    echo "OK: http://127.0.0.1:8765/healthz"
    break
  fi
  sleep 2
done

echo ""
echo "Next steps (see docs/cloudflare-private-dashboard.md):"
echo "  1. Install cloudflared on this VPS"
echo "  2. cloudflared tunnel login && tunnel create"
echo "  3. Configure ~/.cloudflared/config.yml (infra/cloudflared/config.example.yml)"
echo "  4. Cloudflare Access → allow friends' emails"
echo "  5. Optional: docker compose ... run --rm web ploy-sim init-profiles"
