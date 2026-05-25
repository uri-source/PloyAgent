#!/usr/bin/env bash
# Post-deploy checks on AWS EC2. Run after aws-deploy.sh and ALB/Cognito setup.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.aws.yml)

fail=0
ok() { echo "OK: $*"; }
bad() { echo "FAIL: $*"; fail=1; }

echo "=== PloyAgent AWS verify ==="

if curl -sf http://127.0.0.1:8765/healthz | grep -q '"status":"ok"'; then
  ok "web /healthz"
else
  bad "web /healthz"
fi

if curl -sf http://127.0.0.1:8765/api/sim/tracker >/dev/null; then
  ok "sim tracker API"
else
  bad "sim tracker API"
fi

running=$("${COMPOSE[@]}" ps --services --status running 2>/dev/null | wc -l | tr -d ' ')
if [[ "${running:-0}" -ge 5 ]]; then
  ok "compose services running ($running)"
else
  bad "expected most services running (got $running)"
fi

if command -v docker >/dev/null 2>&1; then
  price_age=$("${COMPOSE[@]}" exec -T timescaledb psql -U postgres -d ploy_agent -tAc \
    "SELECT EXTRACT(EPOCH FROM (NOW() - MAX(ts)))::int FROM prices" 2>/dev/null | tr -d ' ' || echo "")
  if [[ -n "$price_age" && "$price_age" -lt 300 ]]; then
    ok "prices fresh (${price_age}s)"
  else
    bad "prices stale or missing (age=${price_age:-unknown}s) — check ingest / Polymarket egress"
  fi
fi

echo ""
if [[ $fail -eq 0 ]]; then
  echo "All local checks passed. Test HTTPS + Cognito in browser (dashboard URL)."
else
  echo "Some checks failed — see docs/aws-hosting.md"
  exit 1
fi
