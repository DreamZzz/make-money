#!/bin/bash
# 启动 Streamlit Dashboard
# 用法:
#   bash scripts/start_dashboard.sh
#   PORT=8502 bash scripts/start_dashboard.sh
#   PYTHON=/path/to/python bash scripts/start_dashboard.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

PYTHON="${PYTHON:-python3}"
PORT="${PORT:-8501}"
ADDRESS="${ADDRESS:-localhost}"
BROWSER="${BROWSER:-false}"

cd "$PROJECT_DIR"
export PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}"

echo "=== 启动量化评估系统 Dashboard ==="
echo "项目目录: $PROJECT_DIR"
echo "访问地址: http://$ADDRESS:$PORT"
echo "Python: $PYTHON"
echo

exec "$PYTHON" -m streamlit run src/dashboard/app.py \
  --server.port "$PORT" \
  --server.address "$ADDRESS" \
  --server.headless "$BROWSER"
