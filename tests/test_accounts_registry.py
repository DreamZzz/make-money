import duckdb

from src.accounts.config import AccountConfig
from src.accounts.registry import (
    get_account,
    list_accounts,
    mark_real_candidate,
    seed_default_accounts,
    set_status,
    upsert_account,
)
from src.data_pipeline.loader import init_db


def _conn() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_db(conn)
    return conn


def test_account_config_json_round_trip():
    cfg = AccountConfig(
        models=("alpha158", "trend_following"),
        benchmark_index="000905",
        portfolio_overrides={"allocation": {"core_target_pct": 0.3}},
    )
    restored = AccountConfig.from_json(cfg.to_json())
    assert restored == cfg


def test_account_config_subscribes():
    specific = AccountConfig(models=("alpha158",))
    assert specific.subscribes("alpha158") is True
    assert specific.subscribes("trend_following") is False
    # 空订阅集 = 接收全部模型
    assert AccountConfig().subscribes("anything") is True


def test_account_config_effective_config_deep_merges_overrides():
    base = {"portfolio": {"allocation": {"core_target_pct": 0.6, "satellite_target_pct": 0.4}, "max_stock_positions": 10}}
    cfg = AccountConfig(portfolio_overrides={"allocation": {"core_target_pct": 0.5, "satellite_target_pct": 0.45}})
    eff = cfg.effective_config(base)
    # 覆盖的键被替换，未覆盖的键（max_stock_positions）保留
    assert eff["portfolio"]["allocation"]["core_target_pct"] == 0.5
    assert eff["portfolio"]["allocation"]["satellite_target_pct"] == 0.45
    assert eff["portfolio"]["max_stock_positions"] == 10
    # 原 base 不被修改
    assert base["portfolio"]["allocation"]["core_target_pct"] == 0.6


def test_registry_crud():
    conn = _conn()
    rec = upsert_account(
        conn,
        account_id="acc1",
        name="测试账户",
        config=AccountConfig(models=("alpha158",)),
        initial_capital=500_000,
        description="desc",
    )
    assert rec.account_id == "acc1"
    assert rec.initial_capital == 500_000
    assert rec.status == "ACTIVE"
    assert rec.is_real_candidate is False
    assert rec.config.models == ("alpha158",)

    set_status(conn, "acc1", "PROMOTED")
    mark_real_candidate(conn, "acc1", True)
    reloaded = get_account(conn, "acc1")
    assert reloaded.status == "PROMOTED"
    assert reloaded.is_real_candidate is True
    conn.close()


def test_seed_default_accounts_is_idempotent():
    conn = _conn()
    first = seed_default_accounts(conn)
    assert len(first) == 5
    ids = {r.account_id for r in first}
    assert {"alpha158_pure", "trend_pure", "meanrev_pure", "blended_core_satellite", "regime_gated"} == ids

    # 改一个账户状态后再 seed（overwrite=False）应保留，不被覆盖
    set_status(conn, "alpha158_pure", "PAUSED")
    again = seed_default_accounts(conn)
    assert len(again) == 5
    assert get_account(conn, "alpha158_pure").status == "PAUSED"
    assert len(list_accounts(conn)) == 5

    # regime_gated 启用了 regime_policy
    regime = get_account(conn, "regime_gated")
    assert regime.config.portfolio_overrides["regime_policy"]["enabled"] is True
    conn.close()
