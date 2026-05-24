"""虚拟账户注册表：CRUD + 种子账户。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb

from src.accounts.config import AccountConfig

VALID_STATUS = {"ACTIVE", "PAUSED", "PROMOTED", "ARCHIVED"}
DEFAULT_INITIAL_CAPITAL = 1_000_000.0


@dataclass(frozen=True)
class AccountRecord:
    account_id: str
    name: str
    description: str
    initial_capital: float
    market: str
    config: AccountConfig
    status: str
    is_real_candidate: bool
    inception_date: date | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "name": self.name,
            "description": self.description,
            "initial_capital": self.initial_capital,
            "market": self.market,
            "config": self.config.to_dict(),
            "status": self.status,
            "is_real_candidate": self.is_real_candidate,
            "inception_date": self.inception_date,
        }


def upsert_account(
    conn: duckdb.DuckDBPyConnection,
    account_id: str,
    name: str,
    config: AccountConfig,
    *,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    market: str = "CN",
    description: str = "",
    inception_date: date | None = None,
    status: str = "ACTIVE",
    is_real_candidate: bool = False,
) -> AccountRecord:
    if status not in VALID_STATUS:
        raise ValueError(f"非法账户状态: {status}")
    conn.execute("DELETE FROM virtual_accounts WHERE account_id = ?", [account_id])
    conn.execute(
        """
        INSERT INTO virtual_accounts (
            account_id, name, description, initial_capital, market,
            config_json, status, is_real_candidate, inception_date, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            account_id, name, description, float(initial_capital), market,
            config.to_json(), status, bool(is_real_candidate), inception_date,
        ],
    )
    return get_account(conn, account_id)  # type: ignore[return-value]


def get_account(conn: duckdb.DuckDBPyConnection, account_id: str) -> AccountRecord | None:
    row = conn.execute(
        """
        SELECT account_id, name, description, initial_capital, market,
               config_json, status, is_real_candidate, inception_date
        FROM virtual_accounts WHERE account_id = ?
        """,
        [account_id],
    ).fetchone()
    return _row_to_record(row) if row else None


def list_accounts(
    conn: duckdb.DuckDBPyConnection,
    status: str | None = None,
) -> list[AccountRecord]:
    params: list[Any] = []
    where = ""
    if status is not None:
        where = "WHERE status = ?"
        params.append(status)
    rows = conn.execute(
        f"""
        SELECT account_id, name, description, initial_capital, market,
               config_json, status, is_real_candidate, inception_date
        FROM virtual_accounts {where}
        ORDER BY account_id
        """,
        params,
    ).fetchall()
    return [_row_to_record(r) for r in rows]


def set_status(conn: duckdb.DuckDBPyConnection, account_id: str, status: str) -> None:
    if status not in VALID_STATUS:
        raise ValueError(f"非法账户状态: {status}")
    conn.execute(
        "UPDATE virtual_accounts SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE account_id = ?",
        [status, account_id],
    )


def mark_real_candidate(
    conn: duckdb.DuckDBPyConnection,
    account_id: str,
    value: bool = True,
) -> None:
    conn.execute(
        "UPDATE virtual_accounts SET is_real_candidate = ?, updated_at = CURRENT_TIMESTAMP WHERE account_id = ?",
        [bool(value), account_id],
    )


def _row_to_record(row: tuple) -> AccountRecord:
    return AccountRecord(
        account_id=str(row[0]),
        name=str(row[1]),
        description=str(row[2] or ""),
        initial_capital=float(row[3] or 0.0),
        market=str(row[4] or "CN"),
        config=AccountConfig.from_json(row[5]),
        status=str(row[6] or "ACTIVE"),
        is_real_candidate=bool(row[7]),
        inception_date=row[8],
    )


# ============================================================
# 种子账户：5 套完整可部署配置，覆盖纯模型与组合两类
# ============================================================
_PURE_STOCK_ALLOCATION = {
    "allocation": {"core_target_pct": 0.0, "satellite_target_pct": 0.95, "cash_target_pct": 0.05},
}


def default_account_specs() -> list[dict[str, Any]]:
    """返回种子账户规格。每个账户都是一套完整可部署配置。"""
    return [
        {
            "account_id": "alpha158_pure",
            "name": "Alpha158 纯模型",
            "description": "仅订阅 alpha158 生产模型，全仓个股，baseline 门槛。",
            "config": AccountConfig(
                models=("alpha158",),
                portfolio_overrides=dict(_PURE_STOCK_ALLOCATION),
            ),
        },
        {
            "account_id": "trend_pure",
            "name": "趋势跟随 纯策略",
            "description": "仅订阅 trend_following，全仓个股。",
            "config": AccountConfig(
                models=("trend_following",),
                portfolio_overrides=dict(_PURE_STOCK_ALLOCATION),
            ),
        },
        {
            "account_id": "meanrev_pure",
            "name": "均值回归 纯策略",
            "description": "仅订阅 mean_reversion，全仓个股。",
            "config": AccountConfig(
                models=("mean_reversion",),
                portfolio_overrides=dict(_PURE_STOCK_ALLOCATION),
            ),
        },
        {
            "account_id": "blended_core_satellite",
            "name": "组合 核心-卫星",
            "description": "alpha158 + 趋势 + 均值回归 + 行业轮动，core50/卫星45/现金5，套利共识开启。",
            "config": AccountConfig(
                models=("alpha158", "trend_following", "mean_reversion", "industry_rotation"),
                portfolio_overrides={
                    "allocation": {"core_target_pct": 0.50, "satellite_target_pct": 0.45, "cash_target_pct": 0.05},
                },
            ),
        },
        {
            "account_id": "regime_gated",
            "name": "组合 宏观择时",
            "description": "组合配置叠加 regime_policy：risk-off 时降仓只卖。",
            "config": AccountConfig(
                models=("alpha158", "trend_following", "mean_reversion", "industry_rotation"),
                portfolio_overrides={
                    "allocation": {"core_target_pct": 0.50, "satellite_target_pct": 0.45, "cash_target_pct": 0.05},
                    "regime_policy": {"enabled": True},
                },
            ),
        },
    ]


def seed_default_accounts(
    conn: duckdb.DuckDBPyConnection,
    *,
    initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    market: str = "CN",
    inception_date: date | None = None,
    overwrite: bool = False,
) -> list[AccountRecord]:
    """创建种子账户。overwrite=False 时跳过已存在账户，保护其历史。"""
    records: list[AccountRecord] = []
    for spec in default_account_specs():
        existing = get_account(conn, spec["account_id"])
        if existing is not None and not overwrite:
            records.append(existing)
            continue
        records.append(upsert_account(
            conn,
            account_id=spec["account_id"],
            name=spec["name"],
            config=spec["config"],
            initial_capital=initial_capital,
            market=market,
            description=spec["description"],
            inception_date=inception_date,
        ))
    return records
