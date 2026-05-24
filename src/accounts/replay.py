"""历史回放预热：用历史行情重算信号，给账户回灌可比战绩。

防 look-ahead 是第一原则：
- 规则策略信号由策略模块在历史行情上确定性重算（滚动窗口仅用过去数据）；
- alpha158 信号来自 qlib_predictions 的 production_inference（本就是逐日的盘后推断）；
- 每日套利只用 prediction_date <= 当日 的基准预测（as_of 过滤）；
- 信号在 T+1 开盘成交，持仓按当日收盘 mark-to-market。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import duckdb
import pandas as pd
from loguru import logger

from src.accounts.engine import (
    AccountState,
    _execute_accepted,
    rebuild_account_state,
)
from src.accounts.registry import AccountRecord
from src.config import load_config
from src.portfolio.regime_policy import load_latest_regime_policy
from src.signals.arbiter import (
    ACCEPTED,
    _build_decisions,
    _consensus_baselines,
    _load_latest_baseline_predictions,
)

WARMUP_DAYS = 220  # 策略指标预热缓冲（slow_ma=60 等）


@dataclass(frozen=True)
class ReplayResult:
    account_id: str
    start: date
    end: date
    signal_dates: int
    orders: int
    nav_days: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "start": self.start,
            "end": self.end,
            "signal_dates": self.signal_dates,
            "orders": self.orders,
            "nav_days": self.nav_days,
        }


def _load_price_wide(conn, start: date, end: date) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """加载窗口（含预热缓冲）内 CN 股票的 close/high/low 宽表。"""
    load_start = start - timedelta(days=WARMUP_DAYS)
    df = conn.execute(
        """
        SELECT symbol, trade_date, close, high, low
        FROM daily_price
        WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')
          AND trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date
        """,
        [load_start, end],
    ).fetchdf()
    if df.empty:
        empty = pd.DataFrame()
        return empty, empty, empty
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    close = df.pivot(index="trade_date", columns="symbol", values="close")
    high = df.pivot(index="trade_date", columns="symbol", values="high")
    low = df.pivot(index="trade_date", columns="symbol", values="low")
    return close, high, low


def generate_historical_rule_signals(conn, start: date, end: date) -> pd.DataFrame:
    """在历史行情上确定性重算规则策略信号（全日期），返回窗口内信号。"""
    from src.research.strategies.industry_rotation import generate_rotation_signals as ind_rot_signals
    from src.research.strategies.mean_reversion import generate_signals as mean_rev_signals
    from src.research.strategies.trend_following import compute_signals as trend_signals

    close, high, low = _load_price_wide(conn, start, end)
    if close.empty:
        return pd.DataFrame()

    frames = [trend_signals(close, highs=high, lows=low), mean_rev_signals(close)]
    try:
        ind = conn.execute(
            "SELECT symbol, industry FROM stock_info WHERE industry IS NOT NULL AND country='CN'"
        ).fetchdf()
        if not ind.empty:
            frames.append(ind_rot_signals(close, dict(zip(ind["symbol"], ind["industry"]))))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"行业轮动历史信号跳过: {exc}")

    valid = [f for f in frames if f is not None and not f.empty and "trade_date" in f.columns]
    if not valid:
        return pd.DataFrame()
    out = pd.concat(valid, ignore_index=True)
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.date
    return out[(out["trade_date"] >= start) & (out["trade_date"] <= end)].copy()


def _latest_walk_forward_experiment(conn, mode: str = "walk_forward") -> str | None:
    """选最新的 walk_forward 实验（构造上 point-in-time，覆盖 2024+，防 look-ahead）。"""
    row = conn.execute(
        """
        SELECT experiment_id FROM qlib_predictions WHERE mode = ?
        GROUP BY experiment_id
        ORDER BY MAX(prediction_date) DESC, experiment_id DESC LIMIT 1
        """,
        [mode],
    ).fetchone()
    return row[0] if row else None


def generate_historical_alpha158_signals(
    conn, start: date, end: date, top_n: int = 10,
    mode: str = "walk_forward", experiment_id: str | None = None,
) -> pd.DataFrame:
    """派生 alpha158 历史信号：月度 top-N 轮动（进场 BUY + 出场 SELL）。

    历史回放用 walk_forward 预测（每个日期由仅含过去数据训练的模型预测，防 look-ahead）。
    每月首个预测日取 rank<=top_n 为目标组合：相对上期新进者发 BUY、掉出者发 SELL，
    保留者持有不动。这复刻 alpha158 月度 top-N 调仓口径，避免"买满即不动"的失真。
    score=归一化排名分位（=confidence），与生产信号一致；裸 score 不可直接当 rank_score。
    """
    experiment_id = experiment_id or _latest_walk_forward_experiment(conn, mode)
    if experiment_id is None:
        return pd.DataFrame()
    raw = conn.execute(
        """
        SELECT prediction_date, symbol, rank, confidence
        FROM qlib_predictions
        WHERE mode = ? AND experiment_id = ?
          AND prediction_date >= ? AND prediction_date <= ?
        """,
        [mode, experiment_id, start, end],
    ).fetchdf()
    if raw.empty:
        return pd.DataFrame()
    raw["trade_date"] = pd.to_datetime(raw["prediction_date"]).dt.date
    raw["confidence"] = pd.to_numeric(raw["confidence"], errors="coerce").fillna(0.0)
    raw["ym"] = pd.to_datetime(raw["prediction_date"]).dt.to_period("M")

    weight = round(1.0 / max(top_n, 1), 4)
    rebalance_dates = raw.groupby("ym")["trade_date"].min().tolist()
    prev_holds: set[str] = set()
    records: list[dict[str, Any]] = []
    for rd in sorted(rebalance_dates):
        day = raw[raw["trade_date"] == rd]
        topn = day.nsmallest(int(top_n), "rank")  # rank 升序=越好
        conf_map = dict(zip(topn["symbol"].astype(str), topn["confidence"]))
        top_syms = set(conf_map)
        for sym in top_syms - prev_holds:  # 新进 → BUY
            c = float(conf_map[sym])
            records.append({"model_name": "alpha158", "symbol": sym, "trade_date": rd,
                            "side": "BUY", "score": c, "confidence": c, "max_position_pct": weight})
        for sym in prev_holds - top_syms:  # 掉出 → SELL
            records.append({"model_name": "alpha158", "symbol": sym, "trade_date": rd,
                            "side": "SELL", "score": 1.0, "confidence": 1.0, "max_position_pct": weight})
        prev_holds = top_syms
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def generate_all_historical_signals(conn, start: date, end: date) -> pd.DataFrame:
    """生成全模型历史信号池（一次性，跨账户复用）。

    规则策略重算很重（700 标的×多年），与账户无关，所以只算一次，账户按订阅过滤即可。
    """
    frames = [generate_historical_alpha158_signals(conn, start, end), generate_historical_rule_signals(conn, start, end)]
    valid = [f for f in frames if f is not None and not f.empty]
    if not valid:
        return pd.DataFrame()
    sig = pd.concat(valid, ignore_index=True)
    sig = sig.sort_values("score", ascending=False).drop_duplicates(
        subset=["trade_date", "model_name", "symbol", "side"], keep="first"
    )
    countries = conn.execute(
        "SELECT symbol, COALESCE(country,'CN') AS market FROM stock_info"
    ).fetchdf()
    market_map = dict(zip(countries["symbol"], countries["market"]))
    sig["market"] = sig["symbol"].map(market_map).fillna("CN")
    sig["model_version"] = sig["model_name"] + "-replay"
    sig["signal_ts"] = pd.to_datetime(sig["trade_date"])
    sig["signal_id"] = [f"RPL-{uuid.uuid4().hex[:12]}" for _ in range(len(sig))]
    return sig.reset_index(drop=True)


def build_historical_signals(
    conn, account: AccountRecord, start: date, end: date,
    pool: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """取账户订阅模型的历史信号。pool 给定时直接过滤（避免每账户重算）。"""
    if pool is None:
        pool = generate_all_historical_signals(conn, start, end)
    if pool.empty:
        return pd.DataFrame()
    return pool[pool["model_name"].map(account.config.subscribes)].reset_index(drop=True)


def mark_to_market(conn, state: AccountState, as_of: date) -> None:
    """把持仓价格更新到 as_of 当日收盘（无 look-ahead：只用当日及之前数据）。"""
    if not state.positions:
        return
    syms = list(state.positions.keys())
    placeholders = ",".join(["?"] * len(syms))
    rows = conn.execute(
        f"""
        SELECT symbol, close FROM daily_price
        WHERE symbol IN ({placeholders}) AND trade_date <= ?
        QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY trade_date DESC) = 1
        """,
        [*syms, as_of],
    ).fetchall()
    price_map = {str(s): float(c) for s, c in rows if c is not None}
    for sym, pos in state.positions.items():
        px = price_map.get(sym)
        if px and px > 0:
            pos["price"] = px
            pos["market_value"] = pos["qty"] * px


def replay_account(
    conn: duckdb.DuckDBPyConnection,
    account: AccountRecord,
    start: date,
    end: date,
    config: dict | None = None,
    signal_pool: pd.DataFrame | None = None,
) -> ReplayResult:
    """对单账户做历史回放预热，写 account_orders(source='replay') + account_nav。

    signal_pool 给定时复用全模型信号池（避免每账户重算规则信号）。
    """
    base_config = config or load_config()
    eff_config = account.config.effective_config(base_config)
    baselines = _consensus_baselines(eff_config)

    # 幂等：清掉该账户旧的回放数据（不动前向数据）
    conn.execute("DELETE FROM account_orders WHERE account_id = ? AND source = 'replay'", [account.account_id])
    conn.execute("DELETE FROM account_nav WHERE account_id = ?", [account.account_id])

    signals = build_historical_signals(conn, account, start, end, pool=signal_pool)
    if signals.empty:
        logger.info(f"[{account.account_id}] 回放窗口无历史信号")
        return ReplayResult(account.account_id, start, end, 0, 0, 0)

    state = AccountState(cash=float(account.initial_capital))
    signal_dates = sorted(signals["trade_date"].unique())
    for d in signal_dates:
        day_sig = signals[signals["trade_date"] == d]
        mark_to_market(conn, state, d)
        regime_policy = load_latest_regime_policy(conn, as_of=d, config=eff_config)
        baseline_preds = _load_latest_baseline_predictions(conn, baselines, as_of=d)
        decisions = _build_decisions(day_sig, baseline_preds, eff_config, baselines, regime_policy=regime_policy)
        accepted = decisions[decisions["decision"] == ACCEPTED]
        if accepted.empty:
            continue
        accepted_ids = set(accepted["signal_id"].tolist())
        rows = day_sig[day_sig["signal_id"].isin(accepted_ids)]
        _execute_accepted(conn, account, accepted, eff_config, state, source="replay", signal_rows=rows)

    orders = conn.execute(
        "SELECT COUNT(*) FROM account_orders WHERE account_id = ? AND source = 'replay'",
        [account.account_id],
    ).fetchone()[0]
    nav_days = compute_account_nav(conn, account, start, end)
    return ReplayResult(account.account_id, start, end, len(signal_dates), int(orders), nav_days)


def compute_account_nav(conn, account: AccountRecord, start: date, end: date) -> int:
    """逐交易日按收盘 mark-to-market 计算账户 NAV，写 account_nav。"""
    trading_days = [
        r[0] for r in conn.execute(
            """
            SELECT DISTINCT trade_date FROM daily_price
            WHERE trade_date >= ? AND trade_date <= ?
              AND symbol IN (SELECT symbol FROM stock_info WHERE country='CN')
            ORDER BY trade_date
            """,
            [start, end],
        ).fetchall()
    ]
    if not trading_days:
        return 0
    init_cap = float(account.initial_capital)
    peak = init_cap
    rows = []
    prev_total = init_cap
    for d in trading_days:
        # 用截至当日收盘的累计 FILLED 订单重建现金/持仓，再按当日收盘估值
        state = rebuild_account_state(
            conn, account.account_id, init_cap,
            up_to_ts=pd.Timestamp(d) + pd.Timedelta(hours=23, minutes=59),
        )
        mark_to_market(conn, state, d)
        position_value = state.position_value
        total = state.cash + position_value
        nav = total / init_cap if init_cap > 0 else 1.0
        peak = max(peak, total)
        drawdown = (total - peak) / peak if peak > 0 else 0.0
        daily_return = (total - prev_total) / prev_total if prev_total > 0 else 0.0
        prev_total = total
        rows.append({
            "account_id": account.account_id, "trade_date": d, "nav": nav,
            "daily_return": daily_return, "cash": state.cash,
            "position_value": position_value, "total_value": total, "drawdown": drawdown,
        })
    df = pd.DataFrame(rows)
    conn.execute("DELETE FROM account_nav WHERE account_id = ?", [account.account_id])
    conn.register("nav_df", df)
    conn.execute(
        """
        INSERT INTO account_nav (account_id, trade_date, nav, daily_return, cash, position_value, total_value, drawdown)
        SELECT account_id, trade_date, nav, daily_return, cash, position_value, total_value, drawdown FROM nav_df
        """
    )
    conn.unregister("nav_df")
    return len(rows)


def replay_all_accounts(
    conn: duckdb.DuckDBPyConnection,
    start: date,
    end: date,
    config: dict | None = None,
    status: str = "ACTIVE",
) -> list[ReplayResult]:
    from src.accounts.registry import list_accounts

    # 全模型信号池只算一次，跨账户复用（规则信号重算很重）
    pool = generate_all_historical_signals(conn, start, end)
    results = []
    for account in list_accounts(conn, status=status):
        logger.info(f"回放账户 {account.account_id} [{start} ~ {end}]")
        results.append(replay_account(conn, account, start, end, config=config, signal_pool=pool))
    return results
