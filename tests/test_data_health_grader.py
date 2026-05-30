"""C1: data_source_health 分级 grader 测试。"""
from __future__ import annotations

import duckdb

from src.data_pipeline.loader import init_db
from src.monitoring.data_health_grader import grade_data_source_health


def _seed(conn, *, source, market, operation, status, attempted, updated, day="2026-05-29"):
    conn.execute(
        """
        INSERT INTO data_source_health
          (run_id, source, market, operation, started_at, ended_at, status,
           attempted, updated)
        VALUES (?, ?, ?, ?, CAST(? AS TIMESTAMP), CAST(? AS TIMESTAMP), ?, ?, ?)
        """,
        [f"r-{source}-{market}-{operation}", source, market, operation,
         f"{day} 20:00:00", f"{day} 20:05:00", status, attempted, updated],
    )


def test_backup_active_when_primary_fails_and_backup_ok():
    """C1 真实场景:CN daily_update akshare 主源 0/12, yfinance 备源 598/599。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed(conn, source="akshare", market="CN", operation="daily_update",
          status="DEGRADED", attempted=12, updated=0)
    _seed(conn, source="yfinance", market="CN", operation="daily_update",
          status="DEGRADED", attempted=599, updated=598)
    out = grade_data_source_health(conn)
    assert out["overall"]["today_decidable"] is True
    assert out["overall"]["status"] == "backup_active"
    cn = next(d for d in out["domains"] if d["market"] == "CN")
    assert cn["effective_status"] == "backup_active"
    assert cn["primary_source"] == "akshare"
    assert "akshare" in cn["failed_sources"]
    assert "yfinance" in cn["ok_sources"]


def test_failed_when_no_source_recovers():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed(conn, source="akshare", market="CN", operation="daily_update",
          status="FAILED", attempted=12, updated=0)
    _seed(conn, source="yfinance", market="CN", operation="daily_update",
          status="DEGRADED", attempted=100, updated=20)  # 20% — failed
    out = grade_data_source_health(conn)
    assert out["overall"]["today_decidable"] is False
    assert out["overall"]["status"] == "failed"
    cn = next(d for d in out["domains"] if d["market"] == "CN")
    assert cn["effective_status"] == "failed"


def test_decidable_when_primary_ok():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed(conn, source="akshare", market="CN", operation="daily_update",
          status="OK", attempted=600, updated=600)
    _seed(conn, source="yfinance", market="CN", operation="daily_update",
          status="OK", attempted=600, updated=599)
    out = grade_data_source_health(conn)
    assert out["overall"]["today_decidable"] is True
    assert out["overall"]["status"] == "decidable"
    cn = next(d for d in out["domains"] if d["market"] == "CN")
    assert cn["effective_status"] == "decidable"


def test_degraded_when_only_partial_source():
    """场景:所有源都半量(20%-90%)而无 ok 源 → degraded,关键域不可决策。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    _seed(conn, source="akshare", market="CN", operation="daily_update",
          status="DEGRADED", attempted=100, updated=70)  # 70%
    out = grade_data_source_health(conn)
    cn = next(d for d in out["domains"] if d["market"] == "CN")
    assert cn["effective_status"] == "degraded"
    assert out["overall"]["today_decidable"] is False


def test_only_latest_run_per_source_counted():
    """同一日同 source 的多次 run 只取最新一次。"""
    conn = duckdb.connect(":memory:")
    init_db(conn)
    # 早一次 OK
    conn.execute(
        "INSERT INTO data_source_health (run_id, source, market, operation, "
        "started_at, ended_at, status, attempted, updated) "
        "VALUES ('early','yfinance','CN','daily_update',"
        "TIMESTAMP '2026-05-29 10:00:00', TIMESTAMP '2026-05-29 10:05:00', "
        "'OK', 100, 100)"
    )
    # 晚一次 FAILED — 应被采用
    conn.execute(
        "INSERT INTO data_source_health (run_id, source, market, operation, "
        "started_at, ended_at, status, attempted, updated) "
        "VALUES ('late','yfinance','CN','daily_update',"
        "TIMESTAMP '2026-05-29 20:00:00', TIMESTAMP '2026-05-29 20:05:00', "
        "'DEGRADED', 100, 10)"
    )
    out = grade_data_source_health(conn)
    cn = next(d for d in out["domains"] if d["market"] == "CN")
    # 最新一次 yfinance 失败,域整体不可决策
    assert cn["effective_status"] == "failed"


def test_no_data_returns_safe_default():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    out = grade_data_source_health(conn)
    assert out["as_of"] is None
    assert out["overall"]["status"] == "no_data"
    assert out["domains"] == []
