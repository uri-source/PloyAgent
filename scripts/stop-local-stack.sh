#!/usr/bin/env bash
# Stop background PloyAgent processes started by run-local-stack.sh
set -euo pipefail

for pat in "ploy-ingest" "ploy-enrich" "ploy-reason" "ploy-notify" "ploy-sim forward" "ploy-web"; do
  if pgrep -f "$pat" >/dev/null 2>&1; then
    pkill -f "$pat" || true
    echo "stopped: $pat"
  fi
done

echo "Done."
