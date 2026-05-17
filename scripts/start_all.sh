#!/usr/bin/env bash
# Polymarket Edge Agent — Start All Services
# Usage: bash scripts/start_all.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo ""
echo "=== Polymarket Edge Agent ==="
echo "Starting all services..."
echo ""

# Activate venv if present
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then
    source .venv/Scripts/activate
fi

# Ensure artifacts dir
mkdir -p artifacts

PIDS=()

cleanup() {
    echo ""
    echo "Stopping services..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    echo "All services stopped."
}
trap cleanup EXIT INT TERM

# Start each service in background
for svc in ploy-ingest ploy-enrich ploy-reason ploy-notify ploy-web; do
    $svc > "artifacts/${svc}.log" 2> "artifacts/${svc}.err.log" &
    PIDS+=($!)
    echo "  [OK] ${svc} started (PID $!)"
done

echo ""
echo "=== All services running ==="
echo "  Dashboard:  http://127.0.0.1:8765"
echo "  Slack bot:  listening on port 8766"
echo "  PIDs:       ${PIDS[*]}"
echo ""
echo "Press Ctrl+C to stop all services."
echo ""

# Wait forever (cleanup on signal)
wait
