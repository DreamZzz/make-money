"""Optional Qlib PortAna HTML artifact generation."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import PROJECT_ROOT


def build_portana_report_frame(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series | None = None,
    turnover: float | None = None,
) -> pd.DataFrame:
    """Build the minimal DataFrame contract expected by Qlib report_graph."""
    returns = pd.Series(portfolio_returns, name="return").dropna().sort_index()
    if returns.empty:
        return pd.DataFrame(columns=["return", "cost", "bench", "turnover"]).rename_axis("date")

    if benchmark_returns is None or pd.Series(benchmark_returns).empty:
        bench = pd.Series(0.0, index=returns.index, name="bench")
    else:
        bench = pd.Series(benchmark_returns, name="bench").dropna().sort_index()
        bench = bench.reindex(returns.index)
    report = pd.concat([returns, bench], axis=1, sort=False).dropna(subset=["return"])
    if report.empty:
        return pd.DataFrame(columns=["return", "cost", "bench", "turnover"]).rename_axis("date")
    report["bench"] = pd.to_numeric(report["bench"], errors="coerce").fillna(0.0)
    report["cost"] = 0.0
    report["turnover"] = _daily_turnover(turnover)
    report = report[["return", "cost", "bench", "turnover"]]
    report.index = pd.to_datetime(report.index)
    report.index.name = "date"
    return report


def generate_portana_artifact(
    experiment_id: str,
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series | None = None,
    *,
    output_root: str | Path | None = None,
    turnover: float | None = None,
    report_graph_fn: Callable[..., Any] | None = None,
    import_report_graph: Callable[[], Callable[..., Any] | None] | None = None,
) -> dict[str, Any]:
    """Generate a Qlib PortAna HTML artifact if the optional report API is available."""
    report = build_portana_report_frame(portfolio_returns, benchmark_returns, turnover=turnover)
    if report.empty:
        return {
            "status": "skipped",
            "reason": "empty_report_frame",
            "report_rows": 0,
        }

    graph_fn = report_graph_fn
    if graph_fn is None:
        graph_fn = (import_report_graph or _import_report_graph)()
    if graph_fn is None:
        return {
            "status": "skipped",
            "reason": "qlib.contrib.report.analysis_position.report_graph unavailable",
            "report_rows": int(len(report)),
        }

    try:
        figures = graph_fn(report, show_notebook=False)
        figures = list(figures or [])
        output_dir = Path(output_root or (PROJECT_ROOT / "output" / "qlib_portana")) / str(experiment_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "portana.html"
        _write_figures_html(output_path, experiment_id, figures, report_rows=len(report))
        return {
            "status": "generated",
            "path": str(output_path),
            "figure_count": int(len(figures)),
            "report_rows": int(len(report)),
            "source": "qlib.contrib.report.analysis_position.report_graph",
        }
    except Exception as exc:
        return {
            "status": "error",
            "reason": f"{type(exc).__name__}: {exc}",
            "report_rows": int(len(report)),
        }


def generate_portana_artifact_from_db(
    conn: Any,
    experiment_id: str,
    *,
    output_root: str | Path | None = None,
    report_graph_fn: Callable[..., Any] | None = None,
    import_report_graph: Callable[[], Callable[..., Any] | None] | None = None,
) -> dict[str, Any]:
    """Generate a PortAna artifact from persisted qlib_daily_metrics rows."""
    df = conn.execute(
        """
        SELECT metric_date, portfolio_return, benchmark_return, turnover
        FROM qlib_daily_metrics
        WHERE experiment_id = ?
          AND portfolio_return IS NOT NULL
        ORDER BY metric_date
        """,
        [experiment_id],
    ).fetchdf()
    if df.empty:
        return {
            "status": "skipped",
            "reason": "no_portfolio_return_rows",
            "report_rows": 0,
        }
    df["metric_date"] = pd.to_datetime(df["metric_date"])
    portfolio = df.set_index("metric_date")["portfolio_return"].astype(float)
    benchmark = df.set_index("metric_date")["benchmark_return"].astype(float) if "benchmark_return" in df else None
    turnover = pd.to_numeric(df.get("turnover"), errors="coerce").dropna()
    return generate_portana_artifact(
        experiment_id,
        portfolio,
        benchmark,
        output_root=output_root,
        turnover=float(turnover.iloc[0]) if not turnover.empty else None,
        report_graph_fn=report_graph_fn,
        import_report_graph=import_report_graph,
    )


def update_experiment_portana_artifact(conn: Any, experiment_id: str, artifact: dict[str, Any]) -> None:
    """Persist PortAna artifact metadata inside qlib_experiments.metrics_json."""
    row = conn.execute(
        "SELECT metrics_json FROM qlib_experiments WHERE experiment_id = ?",
        [experiment_id],
    ).fetchone()
    if row is None:
        raise ValueError(f"Experiment not found: {experiment_id}")
    metrics = _json_dict(row[0])
    metrics["portana_artifact"] = artifact
    conn.execute(
        """
        UPDATE qlib_experiments
        SET metrics_json = ?
        WHERE experiment_id = ?
        """,
        [json.dumps(metrics, ensure_ascii=False, default=str), experiment_id],
    )


def _daily_turnover(turnover: float | None) -> float:
    try:
        if turnover is None or pd.isna(turnover):
            return 0.0
        return max(float(turnover), 0.0) / 252
    except Exception:
        return 0.0


def _json_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value.copy()
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _import_report_graph() -> Callable[..., Any] | None:
    try:
        from qlib.contrib.report.analysis_position import report_graph

        return report_graph
    except Exception:
        return None


def _write_figures_html(output_path: Path, experiment_id: str, figures: list[Any], report_rows: int) -> None:
    parts = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'>",
        f"<title>Qlib PortAna Report: {experiment_id}</title>",
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:24px;}"
        "h1{font-size:22px;} .meta{color:#555;margin-bottom:18px;}</style>",
        "</head><body>",
        f"<h1>Qlib PortAna Report: {experiment_id}</h1>",
        f"<div class='meta'>report_rows={int(report_rows)} figure_count={len(figures)}</div>",
    ]
    for idx, fig in enumerate(figures):
        if hasattr(fig, "to_html"):
            parts.append(fig.to_html(include_plotlyjs="include" if idx == 0 else False, full_html=False))
        else:
            parts.append(f"<pre>{fig}</pre>")
    parts.append("</body></html>")
    output_path.write_text("\n".join(parts), encoding="utf-8")
