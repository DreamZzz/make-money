#!/bin/bash
# F1 一次性 nav 回灌脚本 — 等待 eastmoney 反爬熔断恢复后跑一次。
# 由 launchd ~com.quant.etf-nav-refetch 调度,也可手动跑。
set -e
cd "$(dirname "$0")/.."

# 清掉企业代理 (代理对 eastmoney 阻断)
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY

LOG_DIR="output"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/etf_nav_retry.log"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] etf-nav-refetch START" >> "$LOG_FILE"

# 探测 IP 是否解封
PROBE=$(.venv-qlib/bin/python -c "
import akshare as ak
try:
    df = ak.fund_etf_hist_em(symbol='510300', period='daily', start_date='20260520', end_date='20260530')
    print(f'OK rows={len(df)}')
except Exception as e:
    print(f'BLOCKED: {str(e)[:80]}')
" 2>&1)
echo "[probe] $PROBE" >> "$LOG_FILE"

if [[ "$PROBE" == OK* ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] IP unblocked, running full nav refetch" >> "$LOG_FILE"
    .venv-qlib/bin/python -m src.data_pipeline.fund_etf_provider fetch \
        --min-scale-yi 50 --lookback-days 1095 --nav-only >> "$LOG_FILE" 2>&1
    .venv-qlib/bin/python -c "
import duckdb
from src.funds.scanner import scan_funds
from collections import Counter
conn = duckdb.connect('data/duckdb/market.db')
results = scan_funds(conn, persist=True)
print('scanner signal_tag dist:', Counter(r.signal_tag for r in results))
conn.close()
" >> "$LOG_FILE" 2>&1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] FINISHED OK" >> "$LOG_FILE"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Still blocked. Re-run later manually:" >> "$LOG_FILE"
    echo "  bash scripts/refetch_etf_nav.sh" >> "$LOG_FILE"
fi
