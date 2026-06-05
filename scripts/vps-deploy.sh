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
echo "Next steps:"
echo "  1. One-time sim profiles:"
echo "     docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm web ploy-sim init-profiles"
echo "  2. From your Mac, SSH tunnel to the dashboard:"
echo "     ssh -N -L 8765:127.0.0.1:8765 root@YOUR_VPS_IP"
echo "     → http://127.0.0.1:8765/paper"
echo "  3. Full guide: docs/simple-vps-guide.md"
echo "  4. Optional HTTPS for friends: docs/cloudflare-private-dashboard.md"
