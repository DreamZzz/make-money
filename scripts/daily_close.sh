#!/bin/bash
# 收盘一站式：更新行情 → 持仓基础信息 → 指数基金 → 生成信号 → 资金分配计划 → 纸交易 → 计算净值 → 信号收益跟踪 → 模型监控
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
echo "1/11 暂停 Dashboard..."
launchctl unload "$DASHBOARD" 2>/dev/null || true
_dashboard_stopped=1
trap _restart_dashboard EXIT
sleep 3

# 2. 拉取最新行情
echo "2/11 更新行情数据..."
"$PYTHON" -m src.data_pipeline.main update

# 3. 补当前持仓基础信息（失败不阻塞收盘闭环）
echo "3/11 补当前持仓基础信息..."
"$PYTHON" -m src.portfolio.fundamentals_coverage update || true

# 4. 更新指数基金
echo "4/11 更新指数基金数据..."
"$PYTHON" -m src.index_funds.pipeline update

# 5. 生成指数基金信号
echo "5/11 生成指数基金信号..."
"$PYTHON" -m src.index_funds.signals generate

# 6. 生成股票信号
echo "6/11 生成交易信号..."
"$PYTHON" -m src.signals.generator

# 7. Qlib production 日常推理（无 production 模型时自动跳过）
echo "7/11 Qlib production 日常推理..."
"$PYTHON" -m src.backtest.qlib_runner predict-latest --model production || true

# 8. 统一资金分配计划
echo "8/11 生成统一资金分配计划..."
"$PYTHON" -m src.portfolio.allocator plan

# 9. 纸交易
echo "9/11 执行纸交易 & 计算净值..."
"$PYTHON" -m src.portfolio.paper_engine
"$PYTHON" -m src.portfolio.nav_calculator

# 10. 信号收益跟踪
echo "10/11 更新信号收益跟踪..."
"$PYTHON" -m src.signals.outcome_tracker update

# 11. 生产模型监控
echo "11/11 生产模型监控..."
"$PYTHON" -m src.monitoring.model_monitor update

# 重启 Dashboard
_restart_dashboard
trap - EXIT

echo "=== $(date '+%H:%M:%S') 收盘处理完成 ==="
