"""CLI pipeline for independent index-fund data updates."""
from __future__ import annotations

import click
import pandas as pd
from loguru import logger

from src.index_funds.config import get_watchlist, watchlist_to_frame
from src.index_funds.fetcher import default_fetch_dates, fetch_fund_nav


def update_index_funds() -> dict:
    from src.config import load_config
    from src.data_pipeline.loader import get_connection, init_db, upsert_fund_info, upsert_fund_nav

    config = load_config()
    index_fund_cfg = config.get("index_funds", {})
    if not index_fund_cfg.get("enabled", True):
        logger.info("Index fund module disabled")
        return {"funds": 0, "nav_rows": 0}

    watchlist = get_watchlist(index_fund_cfg)
    configured = [item for item in watchlist if item.fund_code]
    conn = get_connection()
    try:
        init_db(conn)
        info_df = watchlist_to_frame(configured)
        if not info_df.empty:
            info_df = info_df.rename(columns={"tracking_index_name": "_tracking_index_name"})
            upsert_fund_info(conn, info_df)

        history_years = int(config.get("data", {}).get("history_years", 5))
        start_date, end_date = default_fetch_dates(history_years)
        nav_frames = []
        for item in configured:
            nav = fetch_fund_nav(item, start_date=start_date, end_date=end_date)
            if not nav.empty:
                nav_frames.append(nav)
        nav_rows = 0
        if nav_frames:
            nav_df = pd.concat(nav_frames, ignore_index=True)
            nav_rows = upsert_fund_nav(conn, nav_df)
        logger.info(f"Index fund update complete: funds={len(configured)}, nav_rows={nav_rows}")
        return {"funds": len(configured), "nav_rows": nav_rows}
    finally:
        conn.close()


@click.group()
def cli():
    pass


@cli.command()
def update():
    """Update configured index fund NAV/ETF history."""
    stats = update_index_funds()
    click.echo(f"index funds updated: funds={stats['funds']} nav_rows={stats['nav_rows']}")


if __name__ == "__main__":
    cli()

