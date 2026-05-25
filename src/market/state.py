"""市场状态引擎：把价格/宽度/热度/相对强弱/估值综合成一个每日"市场体检"。

输出 stage(阶段标签) + stage_score(趋势分 -100~100) + heat_score(热度 0-100)
+ 宽度/分化/量能/估值分位/相对强弱 + 一句中文解读，落 market_state 表。
B(仓位信号) 与 A(指数搭配) 都消费它。所有指标只用截至当日数据，无 look-ahead。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from loguru import logger

TRADING_DAYS = 252
RS_INDEXES = ["000300", "000905", "HSTECH"]
RS_NAMES = {"000300": "沪深300", "000905": "中证500", "HSTECH": "恒生科技"}


@dataclass
class MarketState:
    trade_date: date
    benchmark: str
    stage: str
    stage_score: float
    heat_score: float
    breadth_above_ma50: float | None
    breadth_above_ma200: float | None
    advance_ratio: float | None
    new_high_low_ratio: float | None
    dispersion: float | None
    volume_ratio: float | None
    limit_up_ratio: float | None
    pe_pct_10y: float | None
    pb_pct_10y: float | None
    rs_leader: str | None
    rs_json: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_stage(close: pd.Series) -> tuple[str, float]:
    """从指数收盘序列判断趋势阶段与趋势分(-100~100)，无 look-ahead。"""
    close = pd.to_numeric(close, errors="coerce").dropna()
    if len(close) < 20:
        return "数据不足", 0.0
    last = float(close.iloc[-1])
    ma20 = float(close.rolling(20, min_periods=1).mean().iloc[-1])
    ma60 = float(close.rolling(60, min_periods=1).mean().iloc[-1])
    ma120 = float(close.rolling(120, min_periods=1).mean().iloc[-1])
    high_250 = float(close.tail(250).max())
    drawdown = last / high_250 - 1.0 if high_250 > 0 else 0.0
    rv = float(close.pct_change().tail(20).std() * np.sqrt(TRADING_DAYS)) if len(close) > 2 else 0.0

    score = 0.0
    score += 20 if last > ma20 else -20
    score += 20 if last > ma60 else -20
    score += 20 if last > ma120 else -20
    score += 20 if (ma20 >= ma60 >= ma120) else (-20 if (ma20 <= ma60 <= ma120) else 0)
    # 回撤项：>-5% 记 +20，到 -25% 记 -20，线性
    dd_score = float(np.clip(20 + (drawdown + 0.05) / 0.20 * 40, -20, 20))
    score += dd_score
    score = float(np.clip(score, -100, 100))

    if drawdown <= -0.30 and rv >= 0.35:
        stage = "危机"
    elif score >= 60:
        stage = "强势上升"
    elif score >= 20:
        stage = "温和上升"
    elif score > -20:
        stage = "震荡整理"
    elif score > -60:
        stage = "弱势整理"
    else:
        stage = "下跌"
    return stage, round(score, 1)


def compute_breadth(panel: pd.DataFrame) -> dict[str, float | None]:
    """panel: index=trade_date, columns=symbol 的收盘价宽表（含足够历史算 MA200）。"""
    if panel.empty or len(panel) < 2:
        return {"above_ma50": None, "above_ma200": None, "advance_ratio": None,
                "new_high_low_ratio": None, "dispersion": None, "limit_up_ratio": None}
    last = panel.iloc[-1]
    valid = last.dropna()
    n = len(valid)
    if n == 0:
        return {"above_ma50": None, "above_ma200": None, "advance_ratio": None,
                "new_high_low_ratio": None, "dispersion": None, "limit_up_ratio": None}
    ma50 = panel.rolling(50, min_periods=20).mean().iloc[-1]
    ma200 = panel.rolling(200, min_periods=60).mean().iloc[-1]
    above50 = float((valid > ma50.reindex(valid.index)).mean())
    above200 = float((valid > ma200.reindex(valid.index)).mean())
    ret = panel.pct_change().iloc[-1].reindex(valid.index)
    advance = float((ret > 0).mean())
    limit_up = float((ret > 0.095).mean())
    dispersion = float(ret.std() * np.sqrt(TRADING_DAYS)) if n > 1 else None
    high60 = panel.tail(60).max().reindex(valid.index)
    low60 = panel.tail(60).min().reindex(valid.index)
    new_high = (valid >= high60).sum()
    new_low = (valid <= low60).sum()
    nhl = float(new_high / (new_high + new_low)) if (new_high + new_low) > 0 else None
    return {"above_ma50": above50, "above_ma200": above200, "advance_ratio": advance,
            "new_high_low_ratio": nhl, "dispersion": dispersion, "limit_up_ratio": limit_up}


def compute_volume_ratio(volume: pd.Series) -> float | None:
    v = pd.to_numeric(volume, errors="coerce").dropna()
    if len(v) < 20:
        return None
    avg60 = float(v.tail(60).mean())
    return round(float(v.iloc[-1]) / avg60, 3) if avg60 > 0 else None


def compute_relative_strength(closes: dict[str, pd.Series], window: int = 60) -> tuple[str | None, dict[str, float]]:
    rs: dict[str, float] = {}
    for code, s in closes.items():
        s = pd.to_numeric(s, errors="coerce").dropna()
        if len(s) > window:
            rs[code] = round(float(s.iloc[-1] / s.iloc[-window - 1] - 1.0), 4)
    if not rs:
        return None, {}
    leader = max(rs, key=rs.get)
    return leader, rs


def compute_heat_score(volume_ratio: float | None, limit_up: float | None,
                       dispersion: float | None, advance: float | None) -> float:
    """0-100 热度分：50=常态，越高越热(过热风险)，越低越冷(冰点)。"""
    parts: list[float] = []
    if volume_ratio is not None:
        parts.append(float(np.clip(50 + (volume_ratio - 1.0) * 50, 0, 100)))
    if limit_up is not None:
        parts.append(float(np.clip(limit_up / 0.03 * 50, 0, 100)))
    if advance is not None:
        parts.append(float(np.clip(advance * 100, 0, 100)))
    if dispersion is not None:
        parts.append(float(np.clip(dispersion / 0.40 * 50, 0, 100)))
    return round(float(np.mean(parts)), 1) if parts else 50.0


def _summary(state: dict[str, Any]) -> str:
    pe = state.get("pe_pct_10y")
    val = "估值未知" if pe is None else (f"估值偏贵(PE近10年{pe*100:.0f}分位)" if pe >= 0.7
           else f"估值偏低(PE{pe*100:.0f}分位)" if pe <= 0.3 else f"估值中性(PE{pe*100:.0f}分位)")
    heat = state["heat_score"]
    heat_txt = "情绪过热" if heat >= 70 else "情绪冰点" if heat <= 30 else "情绪中性"
    b200 = state.get("breadth_above_ma200")
    breadth_txt = f"{b200*100:.0f}%个股在年线上" if b200 is not None else "宽度未知"
    leader = RS_NAMES.get(state.get("rs_leader") or "", state.get("rs_leader") or "—")
    return f"{state['stage']}（趋势分{state['stage_score']:.0f}），{breadth_txt}，{heat_txt}，{val}，相对强弱领先：{leader}"


def build_market_state(
    conn: duckdb.DuckDBPyConnection,
    as_of: date | None = None,
    benchmark: str = "000300",
    persist: bool = True,
) -> MarketState | None:
    as_of = as_of or _latest_trade_date(conn)
    if as_of is None:
        return None
    idx = conn.execute(
        "SELECT trade_date, close, volume FROM index_daily WHERE index_code=? AND trade_date<=? "
        "ORDER BY trade_date DESC LIMIT 300", [benchmark, as_of],
    ).fetchdf().sort_values("trade_date")
    if idx.empty:
        return None
    stage, stage_score = compute_stage(idx["close"])
    volume_ratio = compute_volume_ratio(idx["volume"])

    panel = _load_stock_panel(conn, as_of)
    breadth = compute_breadth(panel)

    rs_closes = {}
    for code in RS_INDEXES:
        s = conn.execute(
            "SELECT trade_date, close FROM index_daily WHERE index_code=? AND trade_date<=? "
            "ORDER BY trade_date DESC LIMIT 130", [code, as_of],
        ).fetchdf().sort_values("trade_date")
        if not s.empty:
            rs_closes[code] = s.set_index("trade_date")["close"]
    rs_leader, rs = compute_relative_strength(rs_closes)

    val = conn.execute(
        "SELECT pe_ttm_pct_10y, pb_pct_10y FROM market_valuation WHERE trade_date<=? "
        "ORDER BY trade_date DESC LIMIT 1", [as_of],
    ).fetchone()
    pe_pct = float(val[0]) if val and val[0] is not None else None
    pb_pct = float(val[1]) if val and val[1] is not None else None

    heat = compute_heat_score(volume_ratio, breadth["limit_up_ratio"], breadth["dispersion"], breadth["advance_ratio"])
    state_dict = {
        "trade_date": as_of, "benchmark": benchmark, "stage": stage, "stage_score": stage_score,
        "heat_score": heat, "breadth_above_ma50": breadth["above_ma50"],
        "breadth_above_ma200": breadth["above_ma200"], "advance_ratio": breadth["advance_ratio"],
        "new_high_low_ratio": breadth["new_high_low_ratio"], "dispersion": breadth["dispersion"],
        "volume_ratio": volume_ratio, "limit_up_ratio": breadth["limit_up_ratio"],
        "pe_pct_10y": pe_pct, "pb_pct_10y": pb_pct, "rs_leader": rs_leader,
        "rs_json": json.dumps(rs, ensure_ascii=False),
    }
    state_dict["summary"] = _summary(state_dict)
    state = MarketState(**state_dict)
    if persist:
        _persist(conn, state)
    return state


def backfill_market_state(
    conn: duckdb.DuckDBPyConnection,
    start: date,
    end: date,
    benchmark: str = "000300",
) -> int:
    """回灌历史 market_state（趋势图用）。面板滚动指标只算一次，逐日切片，高效。"""

    idx = conn.execute(
        "SELECT trade_date, close, volume FROM index_daily WHERE index_code=? AND trade_date<=? "
        "AND trade_date>=? - INTERVAL '400 days' ORDER BY trade_date",
        [benchmark, end, start],
    ).fetchdf()
    if idx.empty:
        return 0
    idx["trade_date"] = pd.to_datetime(idx["trade_date"])
    idx = idx.set_index("trade_date")

    panel = conn.execute(
        """
        SELECT trade_date, symbol, close FROM daily_price
        WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')
          AND trade_date <= ? AND trade_date >= ? - INTERVAL '400 days'
        """,
        [end, start],
    ).fetchdf()
    if panel.empty:
        return 0
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel = panel.pivot_table(index="trade_date", columns="symbol", values="close").sort_index()
    ma50 = panel.rolling(50, min_periods=20).mean()
    ma200 = panel.rolling(200, min_periods=60).mean()
    rets = panel.pct_change()

    rs_series = {}
    for code in RS_INDEXES:
        s = conn.execute(
            "SELECT trade_date, close FROM index_daily WHERE index_code=? AND trade_date<=? "
            "AND trade_date>=? - INTERVAL '400 days' ORDER BY trade_date", [code, end, start],
        ).fetchdf()
        if not s.empty:
            rs_series[code] = s.assign(trade_date=pd.to_datetime(s["trade_date"])).set_index("trade_date")["close"]

    val = conn.execute(
        "SELECT trade_date, pe_ttm_pct_10y, pb_pct_10y FROM market_valuation WHERE trade_date<=? ORDER BY trade_date",
        [end],
    ).fetchdf()
    if not val.empty:
        val["trade_date"] = pd.to_datetime(val["trade_date"])

    days = [d for d in panel.index if start <= d.date() <= end]
    rows = []
    for d in days:
        stage, stage_score = compute_stage(idx.loc[idx.index <= d, "close"])
        vr = compute_volume_ratio(idx.loc[idx.index <= d, "volume"])
        last = panel.loc[d].dropna()
        if last.empty:
            continue
        a50 = float((last > ma50.loc[d].reindex(last.index)).mean())
        a200 = float((last > ma200.loc[d].reindex(last.index)).mean())
        r = rets.loc[d].reindex(last.index)
        advance = float((r > 0).mean())
        limit_up = float((r > 0.095).mean())
        dispersion = float(r.std() * np.sqrt(TRADING_DAYS)) if len(r) > 1 else None
        heat = compute_heat_score(vr, limit_up, dispersion, advance)
        rs_now = {c: s.loc[s.index <= d] for c, s in rs_series.items()}
        rs_leader, rs = compute_relative_strength({c: s for c, s in rs_now.items() if len(s) > 60})
        pe_pct = pb_pct = None
        if not val.empty:
            asof = val[val["trade_date"] <= d]
            if not asof.empty:
                pe_pct = _as_float(asof.iloc[-1]["pe_ttm_pct_10y"])
                pb_pct = _as_float(asof.iloc[-1]["pb_pct_10y"])
        sd = {
            "trade_date": d.date(), "benchmark": benchmark, "stage": stage, "stage_score": stage_score,
            "heat_score": heat, "breadth_above_ma50": round(a50, 4), "breadth_above_ma200": round(a200, 4),
            "advance_ratio": round(advance, 4), "new_high_low_ratio": None,
            "dispersion": round(dispersion, 4) if dispersion is not None else None,
            "volume_ratio": vr, "limit_up_ratio": round(limit_up, 4),
            "pe_pct_10y": pe_pct, "pb_pct_10y": pb_pct, "rs_leader": rs_leader,
            "rs_json": json.dumps(rs, ensure_ascii=False),
        }
        sd["summary"] = _summary(sd)
        rows.append(sd)
    if not rows:
        return 0
    df = pd.DataFrame(rows)
    conn.execute("DELETE FROM market_state WHERE benchmark=? AND trade_date BETWEEN ? AND ?", [benchmark, start, end])
    conn.register("_ms_df", df)
    conn.execute(
        """
        INSERT INTO market_state (trade_date, benchmark, stage, stage_score, heat_score,
            breadth_above_ma50, breadth_above_ma200, advance_ratio, new_high_low_ratio, dispersion,
            volume_ratio, limit_up_ratio, pe_pct_10y, pb_pct_10y, rs_leader, rs_json, summary)
        SELECT trade_date, benchmark, stage, stage_score, heat_score, breadth_above_ma50,
            breadth_above_ma200, advance_ratio, new_high_low_ratio, dispersion, volume_ratio,
            limit_up_ratio, pe_pct_10y, pb_pct_10y, rs_leader, rs_json, summary FROM _ms_df
        """
    )
    conn.unregister("_ms_df")
    return len(rows)


def _as_float(v: Any) -> float | None:
    try:
        return None if v is None or pd.isna(v) else float(v)
    except (TypeError, ValueError):
        return None


def load_market_state_history(conn, benchmark: str = "000300", limit: int = 120) -> list[dict[str, Any]]:
    """取最近 N 个交易日的 stage_score/heat_score 序列（趋势图用）。"""
    rows = conn.execute(
        "SELECT trade_date, stage_score, heat_score FROM market_state WHERE benchmark=? "
        "ORDER BY trade_date DESC LIMIT ?", [benchmark, limit],
    ).fetchall()
    out = [{"date": _date_to_iso(d), "stage_score": float(s) if s is not None else None,
            "heat_score": float(h) if h is not None else None} for d, s, h in rows]
    return list(reversed(out))


def _date_to_iso(d: Any) -> str | None:
    return d.isoformat() if hasattr(d, "isoformat") else (str(d) if d is not None else None)


def _latest_trade_date(conn) -> date | None:
    row = conn.execute("SELECT MAX(trade_date) FROM index_daily").fetchone()
    return row[0] if row and row[0] else None


def _load_stock_panel(conn, as_of: date) -> pd.DataFrame:
    df = conn.execute(
        """
        SELECT trade_date, symbol, close FROM daily_price
        WHERE symbol IN (SELECT symbol FROM stock_info WHERE country='CN')
          AND trade_date <= ? AND trade_date >= ? - INTERVAL '400 days'
        """,
        [as_of, as_of],
    ).fetchdf()
    if df.empty:
        return pd.DataFrame()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.pivot_table(index="trade_date", columns="symbol", values="close").sort_index()


def _persist(conn, state: MarketState) -> None:
    conn.execute("DELETE FROM market_state WHERE trade_date=? AND benchmark=?", [state.trade_date, state.benchmark])
    conn.execute(
        """
        INSERT INTO market_state (
            trade_date, benchmark, stage, stage_score, heat_score, breadth_above_ma50,
            breadth_above_ma200, advance_ratio, new_high_low_ratio, dispersion, volume_ratio,
            limit_up_ratio, pe_pct_10y, pb_pct_10y, rs_leader, rs_json, summary
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [state.trade_date, state.benchmark, state.stage, state.stage_score, state.heat_score,
         state.breadth_above_ma50, state.breadth_above_ma200, state.advance_ratio,
         state.new_high_low_ratio, state.dispersion, state.volume_ratio, state.limit_up_ratio,
         state.pe_pct_10y, state.pb_pct_10y, state.rs_leader, state.rs_json, state.summary],
    )


def load_latest_market_state(conn, benchmark: str = "000300") -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM market_state WHERE benchmark=? ORDER BY trade_date DESC LIMIT 1", [benchmark],
    ).fetchdf()
    if row.empty:
        return None
    rec = row.iloc[0].to_dict()
    rec["trade_date"] = rec["trade_date"].date().isoformat() if hasattr(rec["trade_date"], "date") else str(rec["trade_date"])
    return rec


def main(argv: list[str] | None = None) -> int:
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        state = build_market_state(conn)
        if state:
            logger.info(f"市场状态 {state.trade_date}: {state.summary}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
