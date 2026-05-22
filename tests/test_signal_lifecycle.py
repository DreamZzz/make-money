from __future__ import annotations

import duckdb
import pandas as pd

from src.data_pipeline.loader import init_db
from src.signals.lifecycle import retire_same_day_replaced_signals


def test_retire_same_day_replaced_signals_supersedes_non_filled_same_key():
    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO signals (
            signal_id, model_name, model_version, symbol, signal_ts,
            side, score, confidence, executed, status, status_reason
        )
        VALUES
          ('old_active', 'trend_following', '1.0', '000001',
           TIMESTAMP '2026-05-20 00:00:00', 'BUY', 0.7, 0.8, FALSE, 'ACTIVE', NULL),
          ('old_no_action', 'trend_following', '1.0', '000001',
           TIMESTAMP '2026-05-20 00:00:00', 'BUY', 0.6, 0.7, TRUE, 'NO_ACTION', '旧仲裁拒绝'),
          ('old_filled', 'trend_following', '1.0', '000002',
           TIMESTAMP '2026-05-20 00:00:00', 'BUY', 0.9, 0.9, TRUE, 'FILLED', '成交')
    """)
    new_signals = pd.DataFrame([
        {
            "signal_id": "new_buy",
            "model_name": "trend_following",
            "symbol": "000001",
            "side": "BUY",
            "signal_ts": pd.Timestamp("2026-05-20"),
        },
        {
            "signal_id": "new_filled_key",
            "model_name": "trend_following",
            "symbol": "000002",
            "side": "BUY",
            "signal_ts": pd.Timestamp("2026-05-20"),
        },
    ])

    count = retire_same_day_replaced_signals(conn, new_signals)

    rows = conn.execute("""
        SELECT signal_id, status, superseded_by
        FROM signals
        ORDER BY signal_id
    """).fetchall()
    assert count == 2
    assert rows == [
        ("old_active", "SUPERSEDED", "new_buy"),
        ("old_filled", "FILLED", None),
        ("old_no_action", "SUPERSEDED", "new_buy"),
    ]
    conn.close()
