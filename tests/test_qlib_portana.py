from pathlib import Path

import pandas as pd

from src.backtest.qlib_portana import (
    build_portana_report_frame,
    generate_portana_artifact,
    generate_portana_artifact_from_db,
    update_experiment_portana_artifact,
)
from src.data_pipeline.loader import init_db


class _FakeFigure:
    def __init__(self, label: str):
        self.label = label

    def to_html(self, include_plotlyjs=True, full_html=False):
        return f"<div>{self.label}:{include_plotlyjs}:{full_html}</div>"


def test_build_portana_report_frame_matches_qlib_report_contract():
    strategy = pd.Series(
        [0.01, -0.02],
        index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
    )
    benchmark = pd.Series(
        [0.003, 0.004],
        index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
    )

    report = build_portana_report_frame(strategy, benchmark, turnover=12.6)

    assert report.index.name == "date"
    assert report.columns.tolist() == ["return", "cost", "bench", "turnover"]
    assert report.iloc[0]["return"] == 0.01
    assert report.iloc[0]["bench"] == 0.003
    assert report.iloc[0]["turnover"] == 12.6 / 252


def test_generate_portana_artifact_writes_html_with_injected_report_graph(tmp_path: Path):
    strategy = pd.Series(
        [0.01, -0.02],
        index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
    )
    benchmark = pd.Series(
        [0.003, 0.004],
        index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
    )

    result = generate_portana_artifact(
        "EXP1",
        strategy,
        benchmark,
        output_root=tmp_path,
        turnover=12.6,
        report_graph_fn=lambda report_df, show_notebook=False: [_FakeFigure("nav"), _FakeFigure("risk")],
    )

    html = Path(result["path"]).read_text(encoding="utf-8")
    assert result["status"] == "generated"
    assert result["figure_count"] == 2
    assert result["report_rows"] == 2
    assert result["path"].endswith("EXP1/portana.html")
    assert "Qlib PortAna Report: EXP1" in html
    assert "nav:include:False" in html
    assert "risk:False:False" in html


def test_generate_portana_artifact_skips_when_qlib_api_missing(tmp_path: Path):
    result = generate_portana_artifact(
        "EXP2",
        pd.Series([0.01], index=pd.to_datetime(["2026-01-02"])),
        pd.Series([0.003], index=pd.to_datetime(["2026-01-02"])),
        output_root=tmp_path,
        import_report_graph=lambda: None,
    )

    assert result["status"] == "skipped"
    assert "report_graph" in result["reason"]


def test_generate_from_db_and_update_experiment_metrics_json(tmp_path: Path):
    import duckdb

    conn = duckdb.connect(":memory:")
    init_db(conn)
    conn.execute("""
        INSERT INTO qlib_experiments (experiment_id, model_name, mode, status, metrics_json)
        VALUES ('EXP3', 'alpha158', 'walk_forward', 'SUCCEEDED', '{"annual_return": 0.1}')
    """)
    conn.execute("""
        INSERT INTO qlib_daily_metrics (
            experiment_id, metric_date, mode, portfolio_return, benchmark_return, turnover
        )
        VALUES
            ('EXP3', DATE '2026-01-02', 'walk_forward', 0.01, 0.003, 12.6),
            ('EXP3', DATE '2026-01-05', 'walk_forward', -0.02, 0.004, 12.6)
    """)

    artifact = generate_portana_artifact_from_db(
        conn,
        "EXP3",
        output_root=tmp_path,
        report_graph_fn=lambda report_df, show_notebook=False: [_FakeFigure("db")],
    )
    update_experiment_portana_artifact(conn, "EXP3", artifact)
    metrics = conn.execute("""
        SELECT metrics_json
        FROM qlib_experiments
        WHERE experiment_id = 'EXP3'
    """).fetchone()[0]

    assert artifact["status"] == "generated"
    assert '"annual_return": 0.1' in metrics
    assert '"portana_artifact"' in metrics
    assert "portana.html" in metrics
    conn.close()
