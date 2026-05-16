#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${API_PORT:-8600}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
PYTHON_BIN="${PYTHON:-python3.12}"

mkdir -p "$ROOT_DIR/output"

echo "Starting Dashboard V2 API on http://localhost:${API_PORT}"
"${PYTHON_BIN}" -m uvicorn src.dashboard_v2.api:app \
  --host 0.0.0.0 \
  --port "${API_PORT}" \
  --reload \
  > "$ROOT_DIR/output/dashboard_v2_api.log" 2>&1 &
API_PID=$!

echo "Starting Dashboard V2 frontend on http://localhost:${FRONTEND_PORT}"
npm --prefix "$ROOT_DIR/frontend/dashboard-v2" run dev -- \
  --host 0.0.0.0 \
  --port "${FRONTEND_PORT}" \
  > "$ROOT_DIR/output/dashboard_v2_frontend.log" 2>&1 &
FRONTEND_PID=$!

trap 'kill "$API_PID" "$FRONTEND_PID" 2>/dev/null || true' EXIT

echo "API PID: ${API_PID}"
echo "Frontend PID: ${FRONTEND_PID}"
echo "Logs:"
echo "  $ROOT_DIR/output/dashboard_v2_api.log"
echo "  $ROOT_DIR/output/dashboard_v2_frontend.log"

wait
