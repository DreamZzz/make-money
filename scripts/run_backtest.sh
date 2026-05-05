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

# Step 1: DuckDB → Qlib 二进制格式（保证数据与主库同步）
echo "1/2 导出 Qlib 数据..."
python3 scripts/convert_to_qlib.py --market cn

# Step 2: 训练 + 预测 + 信号写入
echo "2/2 训练 Alpha158 + 写入信号..."
python3 -c "
from src.backtest.qlib_runner import run_qlib_backtest
result = run_qlib_backtest('$STRATEGY')
if result:
    print('IC Mean:', result.get('ic_mean', 'N/A'))
    print('ICIR   :', result.get('icir', 'N/A'))
    print('信号数  :', result.get('signals_written', 0))
"

echo "=== $(date '+%Y-%m-%d %H:%M:%S') 回测完成 ==="
