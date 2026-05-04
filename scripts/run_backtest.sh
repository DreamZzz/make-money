#!/bin/bash
# 回测 & 信号生成脚本
# 用法: bash scripts/run_backtest.sh [strategy_name]
#   strategy_name: alpha158 | trend | industry | mean_rev | all (默认)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
STRATEGY="${1:-all}"

cd "$PROJECT_DIR"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 开始回测: strategy=$STRATEGY ==="

# Qlib 回测（后续阶段实现具体 runner）
python -c "
from src.backtest.qlib_runner import run_qlib_backtest
run_qlib_backtest('$STRATEGY')
"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 回测完成 ==="
