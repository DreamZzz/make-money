#!/usr/bin/env bash
# Start Dashboard V2 with preflight checks, port reuse, and startup diagnostics.
#
# Usage:
#   bash scripts/run_dashboard_v2.sh
#   bash scripts/run_dashboard_v2.sh --status
#   bash scripts/run_dashboard_v2.sh --check
#   API_PORT=8601 FRONTEND_PORT=5174 bash scripts/run_dashboard_v2.sh

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend/dashboard-v2"
OUTPUT_DIR="$ROOT_DIR/output"
API_LOG="$OUTPUT_DIR/dashboard_v2_api.log"
FRONTEND_LOG="$OUTPUT_DIR/dashboard_v2_frontend.log"

API_PORT="${API_PORT:-8600}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
HOST="${HOST:-0.0.0.0}"
CHECK_HOST="${CHECK_HOST:-127.0.0.1}"
PYTHON_BIN="${PYTHON:-}"
DASHBOARD_V2_REUSE_EXISTING="${DASHBOARD_V2_REUSE_EXISTING:-true}"
DASHBOARD_V2_NPM_INSTALL="${DASHBOARD_V2_NPM_INSTALL:-true}"
DASHBOARD_V2_HEALTH_TIMEOUT="${DASHBOARD_V2_HEALTH_TIMEOUT:-45}"

API_URL="http://${CHECK_HOST}:${API_PORT}/api/v2/health"
FRONTEND_URL="http://${CHECK_HOST}:${FRONTEND_PORT}/"
API_PUBLIC_URL="http://localhost:${API_PORT}/api/v2/health"
FRONTEND_PUBLIC_URL="http://localhost:${FRONTEND_PORT}/today"

API_PID=""
FRONTEND_PID=""
STARTED_PIDS=()

_usage() {
  cat <<EOF
Usage:
  bash scripts/run_dashboard_v2.sh [--check|--status|--help]

Environment:
  PYTHON=/path/to/python3.12       Python runtime, auto-detected when unset
  API_PORT=8600                    FastAPI port
  FRONTEND_PORT=5173               Vite frontend port
  HOST=0.0.0.0                     Bind address for both services
  DASHBOARD_V2_REUSE_EXISTING=true Reuse healthy services already on the ports
  DASHBOARD_V2_NPM_INSTALL=true    Run npm install if node_modules is missing
  DASHBOARD_V2_HEALTH_TIMEOUT=45   Seconds to wait for each service
EOF
}

_log() {
  printf '[dashboard-v2] %s\n' "$*"
}

_die() {
  printf '[dashboard-v2] ERROR: %s\n' "$*" >&2
  exit 1
}

_cleanup() {
  if ((${#STARTED_PIDS[@]})); then
    _log "Stopping started Dashboard V2 processes: ${STARTED_PIDS[*]}"
    kill "${STARTED_PIDS[@]}" 2>/dev/null || true
  fi
}

trap _cleanup EXIT INT TERM

_require_command() {
  local command_name="$1"
  command -v "$command_name" >/dev/null 2>&1 || _die "Missing command: $command_name"
}

_python_is_compatible() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

_resolve_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    if _python_is_compatible "$PYTHON_BIN"; then
      printf '%s\n' "$PYTHON_BIN"
      return
    fi
    _die "PYTHON=$PYTHON_BIN is not Python 3.12+"
  fi

  local candidate resolved
  for candidate in python3.12 /opt/homebrew/bin/python3.12 /opt/homebrew/opt/python@3.12/bin/python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      resolved="$(command -v "$candidate")"
      if _python_is_compatible "$resolved"; then
        printf '%s\n' "$resolved"
        return
      fi
    elif [[ -x "$candidate" ]] && _python_is_compatible "$candidate"; then
      printf '%s\n' "$candidate"
      return
    fi
  done

  _die "未找到 Python 3.12+。可用 PYTHON=/path/to/python3.12 bash scripts/run_dashboard_v2.sh 指定。"
}

_resolve_node() {
  command -v node >/dev/null 2>&1 || _die "未找到 node。请先安装 Node.js。"
  command -v npm >/dev/null 2>&1 || _die "未找到 npm。请先安装 Node.js/npm。"
}

_ensure_python_dependencies() {
  "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1 || _die "Python 依赖不完整：需要 fastapi 和 uvicorn。"
import fastapi
import uvicorn
PY
}

_ensure_frontend_dependencies() {
  [[ -f "$FRONTEND_DIR/package.json" ]] || _die "缺少 $FRONTEND_DIR/package.json"
  if [[ -d "$FRONTEND_DIR/node_modules" ]]; then
    return
  fi

  if [[ "$DASHBOARD_V2_NPM_INSTALL" != "true" ]]; then
    _die "缺少 $FRONTEND_DIR/node_modules，且 DASHBOARD_V2_NPM_INSTALL=false。"
  fi

  _log "frontend node_modules 不存在，执行 npm install。"
  npm --prefix "$FRONTEND_DIR" install
}

_url_ok() {
  local url="$1"
  curl -fsS --max-time 5 "$url" >/dev/null 2>&1
}

_port_is_listening() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

_port_owner() {
  local port="$1"
  { lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true; } \
    | awk 'NR > 1 {print $1 " pid=" $2 " " $9}' \
    | paste -sd ';' -
}

_tail_log() {
  local log_file="$1"
  if [[ -f "$log_file" ]]; then
    printf '\n--- tail -80 %s ---\n' "$log_file" >&2
    tail -80 "$log_file" >&2 || true
    printf -- '--- end log ---\n' >&2
  else
    printf '\n(no log file yet: %s)\n' "$log_file" >&2
  fi
}

_wait_for_url() {
  local name="$1"
  local url="$2"
  local log_file="$3"
  local deadline=$((SECONDS + DASHBOARD_V2_HEALTH_TIMEOUT))

  while ((SECONDS < deadline)); do
    if _url_ok "$url"; then
      _log "$name is ready: $url"
      return
    fi
    sleep 1
  done

  _tail_log "$log_file"
  _die "$name did not become ready within ${DASHBOARD_V2_HEALTH_TIMEOUT}s: $url"
}

_needs_start_service() {
  local name="$1"
  local port="$2"
  local health_url="$3"

  if ! _port_is_listening "$port"; then
    return 0
  fi

  local owner
  owner="$(_port_owner "$port")"
  if [[ "$DASHBOARD_V2_REUSE_EXISTING" == "true" ]] && _url_ok "$health_url"; then
    _log "Reusing existing $name on port $port (${owner:-unknown owner})"
    return 1
  fi

  _die "$name port $port is already occupied (${owner:-unknown owner}) but health check failed: $health_url"
}

_preflight() {
  mkdir -p "$OUTPUT_DIR"
  _require_command curl
  _require_command lsof
  _resolve_node
  PYTHON_BIN="$(_resolve_python)"
  _ensure_python_dependencies
  _ensure_frontend_dependencies
}

_start_api() {
  : > "$API_LOG"
  _log "Starting Dashboard V2 API on http://localhost:${API_PORT}"
  (
    cd "$ROOT_DIR"
    export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
    exec "$PYTHON_BIN" -m uvicorn src.dashboard_v2.api:app \
      --host "$HOST" \
      --port "$API_PORT" \
      --reload
  ) >"$API_LOG" 2>&1 &
  API_PID=$!
  STARTED_PIDS+=("$API_PID")
  _log "API PID: $API_PID"
}

_start_frontend() {
  : > "$FRONTEND_LOG"
  _log "Starting Dashboard V2 frontend on http://localhost:${FRONTEND_PORT}"
  npm --prefix "$FRONTEND_DIR" run dev -- \
    --host "$HOST" \
    --port "$FRONTEND_PORT" \
    --strictPort \
    >"$FRONTEND_LOG" 2>&1 &
  FRONTEND_PID=$!
  STARTED_PIDS+=("$FRONTEND_PID")
  _log "Frontend PID: $FRONTEND_PID"
}

_print_status_line() {
  local name="$1"
  local port="$2"
  local url="$3"
  local owner
  owner="$(_port_owner "$port")"
  if _port_is_listening "$port"; then
    if _url_ok "$url"; then
      printf '%-10s healthy  port=%s  %s\n' "$name" "$port" "${owner:-unknown owner}"
    else
      printf '%-10s busy     port=%s  %s\n' "$name" "$port" "${owner:-unknown owner}"
    fi
  else
    printf '%-10s stopped  port=%s\n' "$name" "$port"
  fi
}

_show_status() {
  _print_status_line "API" "$API_PORT" "$API_URL"
  _print_status_line "Frontend" "$FRONTEND_PORT" "$FRONTEND_URL"
}

_start() {
  _preflight

  _log "Project: $ROOT_DIR"
  _log "Python: $PYTHON_BIN"
  _log "Logs:"
  _log "  $API_LOG"
  _log "  $FRONTEND_LOG"

  if _needs_start_service "API" "$API_PORT" "$API_URL"; then
    _start_api
  fi
  if _needs_start_service "Frontend" "$FRONTEND_PORT" "$FRONTEND_URL"; then
    _start_frontend
  fi

  _wait_for_url "API" "$API_URL" "$API_LOG"
  _wait_for_url "Frontend" "$FRONTEND_URL" "$FRONTEND_LOG"

  printf '\nDashboard V2 is ready:\n'
  printf '  Frontend: %s\n' "$FRONTEND_PUBLIC_URL"
  printf '  API:      %s\n\n' "$API_PUBLIC_URL"

  if ((${#STARTED_PIDS[@]})); then
    wait "${STARTED_PIDS[@]}"
  fi
}

MODE="start"
case "${1:-}" in
  --help|-h)
    _usage
    exit 0
    ;;
  --check)
    MODE="check"
    ;;
  --status)
    MODE="status"
    ;;
  "")
    ;;
  *)
    _usage >&2
    _die "Unknown argument: $1"
    ;;
esac

case "$MODE" in
  check)
    _preflight
    _log "Preflight checks passed."
    ;;
  status)
    _require_command curl
    _require_command lsof
    _show_status
    ;;
  start)
    _start
    ;;
esac
