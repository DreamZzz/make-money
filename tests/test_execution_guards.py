from src.portfolio.execution_guards import check_open_tradeable


def test_cn_open_tradeable_blocks_st_suspended_and_limit_open():
    assert not check_open_tradeable(10.0, 9.0, market="CN").tradeable
    assert not check_open_tradeable(10.0, 10.0, market="CN", is_st=True).tradeable
    assert not check_open_tradeable(10.0, 10.0, market="CN", is_suspended=True).tradeable


def test_hk_open_tradeable_does_not_apply_cn_limit_rule():
    result = check_open_tradeable(10.0, 9.0, market="HK")

    assert result.tradeable
    assert result.reason_code is None


def test_open_tradeable_allows_when_pre_close_missing():
    result = check_open_tradeable(10.0, None, market="CN")

    assert result.tradeable
