#!/usr/bin/env bash
# Run the full local agent stack in the background (outside Cursor).
# Logs: artifacts/*.log   Stop: ./scripts/stop-local-stack.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p artifacts

if [[ ! -f .env ]]; then
  echo "Missing .env — copy from .env.example or .env.production.example"
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

if [[ ! -d .venv ]]; then
  echo "Missing .venv — run: python -m venv .venv && pip install -e '.[dev]'"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

start_svc() {
  local name="$1"
  local cmd="$2"
  if pgrep -f "$cmd" >/dev/null 2>&1; then
    echo "  $name: already running"
    return
  fi
  nohup $cmd >> "artifacts/${name}.log" 2>&1 &
  echo "  $name: pid $! → artifacts/${name}.log"
}

echo "Starting PloyAgent stack from $ROOT"
start_svc ingest "ploy-ingest"
start_svc enrich "ploy-enrich"
start_svc reason "ploy-reason"
start_svc notify "ploy-notify"
start_svc sim-forward "ploy-sim forward"
start_svc web "ploy-web"

echo ""
echo "Dashboard: http://127.0.0.1:8765"
if [[ "${SIM_FORWARD_RUN_HOURS:-336}" == "0" ]]; then
  echo "Sim forward: unlimited (SIM_FORWARD_RUN_HOURS=0)"
else
  echo "Sim forward: stops after ${SIM_FORWARD_RUN_HOURS:-336}h (~$(( (${SIM_FORWARD_RUN_HOURS:-336} + 23) / 24 )) days)"
fi
echo "Monitor:   tail -f artifacts/ingest.log artifacts/sim-forward.log"
echo "Stop:      ./scripts/stop-local-stack.sh"
