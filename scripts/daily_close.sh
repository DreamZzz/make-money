#!/bin/bash
# 收盘一站式：更新行情 → 生成信号 → 纸交易 → 计算净值
# 用法: bash scripts/daily_close.sh [strategy_name]

set -e
PYTHON="/opt/homebrew/bin/python3.12"
PROJECT="/Users/zhaoqiang/Documents/Project/make-money"
DASHBOARD="$HOME/Library/LaunchAgents/com.quant.dashboard.plist"

cd "$PROJECT"
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
export no_proxy="*"

echo "=== $(date '+%H:%M:%S') 收盘处理开始 ==="

# 1. 停 Dashboard（释放 DB 锁）
echo "1/4 暂停 Dashboard..."
launchctl unload "$DASHBOARD" 2>/dev/null || true
sleep 3

# 2. 拉取最新行情
echo "2/4 更新行情数据..."
$PYTHON -m src.data_pipeline.main update

# 3. 生成信号
echo "3/4 生成交易信号..."
$PYTHON -m src.signals.generator

# 4. 纸交易
echo "4/4 执行纸交易 & 计算净值..."
$PYTHON -m src.portfolio.paper_engine
$PYTHON -m src.portfolio.nav_calculator

# 重启 Dashboard
echo "重启 Dashboard..."
launchctl load "$DASHBOARD" 2>/dev/null || true

echo "=== $(date '+%H:%M:%S') 收盘处理完成 ==="
