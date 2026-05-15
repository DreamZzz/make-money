#!/bin/bash
# 启动 Streamlit Dashboard
# 用法:
#   bash scripts/start_dashboard.sh
#   PORT=8502 bash scripts/start_dashboard.sh
#   PYTHON=/path/to/python bash scripts/start_dashboard.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

_python_is_compatible() {
  "$1" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 12) else 1)
PY
}

_resolve_python() {
  if [ -n "${PYTHON:-}" ]; then
    if _python_is_compatible "$PYTHON"; then
      echo "$PYTHON"
      return
    fi
    echo "PYTHON=$PYTHON 不是 Python 3.12+；本项目 requires-python >=3.12。" >&2
    exit 1
  fi

  for candidate in python3.12 /opt/homebrew/bin/python3.12 /opt/homebrew/opt/python@3.12/bin/python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      resolved="$(command -v "$candidate")"
      if _python_is_compatible "$resolved"; then
        echo "$resolved"
        return
      fi
    elif [ -x "$candidate" ] && _python_is_compatible "$candidate"; then
      echo "$candidate"
      return
    fi
  done

  echo "未找到 Python 3.12+。请安装 python@3.12，或用 PYTHON=/path/to/python3.12 bash scripts/start_dashboard.sh。" >&2
  exit 1
}

PYTHON="$(_resolve_python)"
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
