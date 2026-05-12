#!/bin/bash
# 回测 & 信号生成脚本
# 用法: bash scripts/run_backtest.sh [strategy_name]
#   strategy_name: alpha158 | trend | industry | mean_rev | all (默认)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
STRATEGY="${1:-all}"
PYTHON="${PYTHON:-python3}"

cd "$PROJECT_DIR"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 开始回测: strategy=$STRATEGY ==="

require_qlib_python() {
  if "$PYTHON" -c "import qlib" >/dev/null 2>&1; then
    return
  fi

  for candidate in python3 python3.12 /opt/homebrew/bin/python3.12; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import qlib" >/dev/null 2>&1; then
      PYTHON="$candidate"
      return
    fi
  done

  echo "未找到可 import qlib 的 Python。请先激活原来的 Qlib 环境，或用 PYTHON=/path/to/python bash scripts/run_backtest.sh alpha158。" >&2
  exit 1
}

run_alpha158() {
  require_qlib_python
  echo "使用 Qlib Python: $PYTHON"

  echo "1/4 检查 Qlib 环境..."
  "$PYTHON" -m src.backtest.qlib_runner status || true

  echo "2/4 导出 Qlib 数据..."
  "$PYTHON" -m src.backtest.qlib_runner prepare-data --market cn || true

  echo "3/4 运行固定切分 Alpha158 实验..."
  "$PYTHON" -m src.backtest.qlib_runner run-experiment --mode fixed || true

  echo "4/5 运行 walk-forward Alpha158 实验..."
  "$PYTHON" -m src.backtest.qlib_runner run-experiment --mode walk_forward || true

  echo "5/5 评估 Top-N / 持仓周期 / 调仓频率网格..."
  "$PYTHON" -m src.backtest.qlib_runner evaluate-grid \
    --experiment-id latest \
    --top-n 20,50,100 \
    --holding-days 1-10 \
    --rebalance daily,monthly \
    --buffer-mult 1.5 || true
}

run_momentum() {
  echo "运行 momentum_topn 快速回测..."
  "$PYTHON" - <<'PY'
from src.backtest.vectorbt_runner import run_momentum_backtest

result = run_momentum_backtest(save_result=True)
if not result:
    raise SystemExit("momentum_topn 未产生结果，请检查 daily_price/index_daily")
print("回测ID :", result.get("run_id", "N/A"))
print("年化收益:", result.get("annual_return", "N/A"))
print("夏普   :", result.get("sharpe_ratio", "N/A"))
PY
}

case "$STRATEGY" in
  alpha158)
    run_alpha158
    ;;
  momentum|momentum_topn)
    run_momentum
    ;;
  all)
    run_alpha158
    run_momentum
    ;;
  trend|industry|mean_rev)
    echo "暂不支持 strategy=$STRATEGY 的真实回测落表；当前只支持 alpha158 和 momentum_topn。" >&2
    exit 2
    ;;
  *)
    echo "未知 strategy=$STRATEGY。可选: alpha158 | momentum_topn | all。" >&2
    exit 2
    ;;
esac

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 回测完成 ==="
