#!/usr/bin/env bash
# Full stop + start with health checks (run in your own terminal, not Cursor sandbox).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

"$ROOT/scripts/stop-local-stack.sh"
sleep 2
"$ROOT/scripts/run-local-stack.sh"
sleep 6

echo ""
echo "=== Processes ==="
pgrep -fl "ploy-" || echo "(none)"

echo ""
echo "=== Health ==="
curl -sf http://127.0.0.1:8765/healthz && echo " web OK" || echo " web DOWN"
curl -sf http://127.0.0.1:8765/api/sim/tracker | python3 -c "
import sys, json
d = json.load(sys.stdin)
r = d.get('current_run') or {}
t = d.get('run_totals') or {}
print(' forward_active:', d.get('forward_active'))
print(' run_id:', r.get('id'), 'planned_end:', (r.get('planned_end') or '')[:19])
print(' trades:', t.get('total_trades'), 'open:', t.get('open'), 'closed:', t.get('closed'))
" 2>/dev/null || echo " tracker API unavailable"

echo ""
echo "=== DB price freshness ==="
set -a
# shellcheck disable=SC1091
source .env
set +a
source .venv/bin/activate
python3 <<'PY'
import asyncio, asyncpg, os
async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    now = await conn.fetchval("SELECT NOW()")
    pmax = await conn.fetchval("SELECT MAX(ts) FROM prices")
    age = (now - pmax).total_seconds() if pmax else None
    print(f"  latest price: {pmax}")
    print(f"  age seconds: {age:.0f}" if age is not None else "  no prices")
    if age is not None and age < 120:
        print("  ingest: OK (fresh)")
    elif age is not None:
        print("  ingest: STALE — check artifacts/ingest.log")
    await conn.close()
asyncio.run(main())
PY

echo ""
echo "Dashboard: http://127.0.0.1:8765"
echo "Logs: tail -f artifacts/ingest.log artifacts/sim-forward.log"
