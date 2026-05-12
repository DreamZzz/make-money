#!/bin/bash
# 收盘一站式：更新行情 → 指数基金 → 生成信号 → 纸交易 → 计算净值
# 用法: bash scripts/daily_close.sh [strategy_name]

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(dirname "$SCRIPT_DIR")"
PYTHON="${PYTHON:-python3}"
DASHBOARD="$HOME/Library/LaunchAgents/com.quant.dashboard.plist"

cd "$PROJECT"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
export no_proxy="*"

echo "=== $(date '+%H:%M:%S') 收盘处理开始 ==="

# 1. 停 Dashboard（释放 DB 锁）
echo "1/6 暂停 Dashboard..."
launchctl unload "$DASHBOARD" 2>/dev/null || true
sleep 3

# 2. 拉取最新行情
echo "2/6 更新行情数据..."
$PYTHON -m src.data_pipeline.main update

# 3. 更新指数基金
echo "3/6 更新指数基金数据..."
$PYTHON -m src.index_funds.pipeline update

# 4. 生成指数基金信号
echo "4/6 生成指数基金信号..."
$PYTHON -m src.index_funds.signals generate

# 5. 生成股票信号
echo "5/7 生成交易信号..."
$PYTHON -m src.signals.generator

# 6. Qlib production 日常推理（无 production 模型时自动跳过）
echo "6/7 Qlib production 日常推理..."
$PYTHON -m src.backtest.qlib_runner predict-latest --model production || true

# 7. 纸交易
echo "7/7 执行纸交易 & 计算净值..."
$PYTHON -m src.portfolio.paper_engine
$PYTHON -m src.portfolio.nav_calculator

# 重启 Dashboard
echo "重启 Dashboard..."
launchctl load "$DASHBOARD" 2>/dev/null || true

echo "=== $(date '+%H:%M:%S') 收盘处理完成 ==="
