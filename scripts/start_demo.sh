#!/usr/bin/env bash
# Starts the demo server. Usage: scripts/start_demo.sh [--fresh]
#   --fresh  delete the sandbox directory first, so the seed is rebuilt from scratch
set -euo pipefail

cd "$(dirname "$0")/.."
export DEMO_SANDBOX_PATH="${DEMO_SANDBOX_PATH:-/tmp/gamification-demo-sandbox}"
export DEMO_MODE=1
# Learner clocks tick in real time (use the Freeze button for repeatable runs). Set to 0 for a frozen clock.
export DEMO_LIVE_CLOCK="${DEMO_LIVE_CLOCK:-1}"

if [[ "${1:-}" == "--fresh" ]]; then
  rm -rf "$DEMO_SANDBOX_PATH"
fi

PY=python
if [[ -x .venv/bin/python ]]; then PY=.venv/bin/python; fi

echo "Learner split view: http://localhost:8000/demo.html"
echo "Admin console:      http://localhost:8000/admin.html"
exec "$PY" -m uvicorn webapp.app:app --port 8000
