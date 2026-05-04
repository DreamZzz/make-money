#!/opt/homebrew/bin/python3.12
"""补全缺失的股票日线数据。只拉已有 index 成分股中缺失的。"""
import sys, time
sys.path.insert(0, '/Users/zhaoqiang/Documents/Project/make-money')

from src.data_pipeline.loader import get_connection, upsert_daily_price
from src.data_pipeline.fetchers import yfinance_fetcher as yf
from datetime import date, timedelta

conn = get_connection()

# 1. 找到 A 股缺失：指数成分股中不在 daily_price 的
existing = {r[0] for r in conn.execute(
    "SELECT DISTINCT symbol FROM daily_price WHERE length(symbol)=6"
).fetchall()}

import akshare as ak
hs300 = set(ak.index_stock_cons("000300")["品种代码"].astype(str))
zz500 = set(ak.index_stock_cons("000905")["品种代码"].astype(str))
target_cn = hs300 | zz500
missing_cn = sorted(target_cn - existing)

# 2. 找到港股缺失
existing_hk = {r[0] for r in conn.execute(
    "SELECT DISTINCT symbol FROM daily_price WHERE length(symbol)<=5"
).fetchall()}

# 恒指 + 恒生科技 硬编码备选（从 main.py 同步）
from src.data_pipeline.main import _HSI_FALLBACK
hsi = set(_HSI_FALLBACK)
hstech = set("00700,09988,03690,01810,09618,09999,09888,09961,02015,01024,00981,02382,06618,09626,00992,01347,01833,02018,03888,09633".split(","))
target_hk = hsi | hstech
missing_hk = sorted(target_hk - existing_hk)

print(f"A股缺失: {len(missing_cn)} 只")
print(f"港股缺失: {len(missing_hk)} 只")

# 3. 批量补全
total = len(missing_cn) + len(missing_hk)
done = 0
end = date.today().strftime('%Y-%m-%d')
start = (date.today() - timedelta(days=365*5)).strftime('%Y-%m-%d')

for sym in missing_cn:
    try:
        df = yf.fetch_cn_daily(sym, start, end)
        if not df.empty:
            upsert_daily_price(conn, df)
        done += 1
        if done % 50 == 0:
            print(f"Progress: {done}/{total}")
        time.sleep(0.3)  # 限频
    except Exception as e:
        print(f"Failed {sym}: {e}")
        time.sleep(1)

for sym in missing_hk:
    try:
        df = yf.fetch_hk_daily(sym, start, end)
        if not df.empty:
            upsert_daily_price(conn, df)
        done += 1
        if done % 10 == 0:
            print(f"Progress: {done}/{total}")
        time.sleep(0.3)
    except Exception as e:
        print(f"Failed {sym}: {e}")
        time.sleep(1)

# 4. 验证
new_cn = conn.execute("SELECT COUNT(DISTINCT symbol) FROM daily_price WHERE length(symbol)=6").fetchone()[0]
new_hk = conn.execute("SELECT COUNT(DISTINCT symbol) FROM daily_price WHERE length(symbol)<=5").fetchone()[0]
print(f"\n补全完成: A股 {len(existing)}→{new_cn}, 港股 {len(existing_hk)}→{new_hk}")
conn.close()
