#!/bin/bash
# 收盘一站式：更新行情 → 指数基金 → 生成信号 → 资金分配计划 → 纸交易 → 计算净值
# 用法: bash scripts/daily_close.sh [strategy_name]

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(dirname "$SCRIPT_DIR")"
DASHBOARD="$HOME/Library/LaunchAgents/com.quant.dashboard.plist"

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

  echo "未找到 Python 3.12+。请安装 python@3.12，或用 PYTHON=/path/to/python3.12 bash scripts/daily_close.sh。" >&2
  exit 1
}

PYTHON="$(_resolve_python)"
_dashboard_stopped=0

_restart_dashboard() {
  if [ "$_dashboard_stopped" = "1" ]; then
    echo "重启 Dashboard..."
    launchctl load "$DASHBOARD" 2>/dev/null || true
    _dashboard_stopped=0
  fi
}

cd "$PROJECT"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
export no_proxy="*"

echo "=== $(date '+%H:%M:%S') 收盘处理开始 ==="
echo "Python: $PYTHON"

# 1. 停 Dashboard（释放 DB 锁）
echo "1/6 暂停 Dashboard..."
launchctl unload "$DASHBOARD" 2>/dev/null || true
_dashboard_stopped=1
trap _restart_dashboard EXIT
sleep 3

# 2. 拉取最新行情
echo "2/6 更新行情数据..."
"$PYTHON" -m src.data_pipeline.main update

# 3. 更新指数基金
echo "3/6 更新指数基金数据..."
"$PYTHON" -m src.index_funds.pipeline update

# 4. 生成指数基金信号
echo "4/6 生成指数基金信号..."
"$PYTHON" -m src.index_funds.signals generate

# 5. 生成股票信号
echo "5/8 生成交易信号..."
"$PYTHON" -m src.signals.generator

# 6. Qlib production 日常推理（无 production 模型时自动跳过）
echo "6/8 Qlib production 日常推理..."
"$PYTHON" -m src.backtest.qlib_runner predict-latest --model production || true

# 7. 统一资金分配计划
echo "7/8 生成统一资金分配计划..."
"$PYTHON" -m src.portfolio.allocator plan

# 8. 纸交易
echo "8/8 执行纸交易 & 计算净值..."
"$PYTHON" -m src.portfolio.paper_engine
"$PYTHON" -m src.portfolio.nav_calculator

# 重启 Dashboard
_restart_dashboard
trap - EXIT

echo "=== $(date '+%H:%M:%S') 收盘处理完成 ==="
