#!/usr/bin/env python3
"""Generate or refresh a Qlib PortAna HTML artifact for a stored experiment."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Qlib PortAna artifact from qlib_daily_metrics.")
    parser.add_argument("--experiment-id", default="latest", help="Experiment id, or 'latest' successful experiment")
    parser.add_argument("--no-update", action="store_true", help="Do not write artifact metadata to metrics_json")
    args = parser.parse_args(argv)

    from src.backtest.qlib_portana import generate_portana_artifact_from_db, update_experiment_portana_artifact
    from src.data_pipeline.loader import get_connection, init_db

    conn = get_connection()
    try:
        init_db(conn)
        experiment_id = args.experiment_id
        if experiment_id == "latest":
            row = conn.execute("""
                SELECT e.experiment_id
                FROM qlib_experiments e
                JOIN qlib_daily_metrics m ON e.experiment_id = m.experiment_id
                WHERE e.status = 'SUCCEEDED'
                  AND m.portfolio_return IS NOT NULL
                GROUP BY e.experiment_id, e.ended_at
                ORDER BY e.ended_at DESC NULLS LAST
                LIMIT 1
            """).fetchone()
            if not row:
                raise SystemExit("No successful Qlib experiment with portfolio_return rows found.")
            experiment_id = row[0]
        artifact = generate_portana_artifact_from_db(conn, experiment_id)
        if not args.no_update:
            update_experiment_portana_artifact(conn, experiment_id, artifact)
    finally:
        conn.close()

    print(json.dumps({"experiment_id": experiment_id, "portana_artifact": artifact}, ensure_ascii=False, indent=2))
    return 0 if artifact.get("status") in {"generated", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
