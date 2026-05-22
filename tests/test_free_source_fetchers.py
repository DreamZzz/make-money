import pandas as pd

from src.data_pipeline.fetchers.free_sources import (
    fetch_cninfo_industry_members,
    fetch_eastmoney_industry_members,
    fetch_legulegu_sw_industry_members,
    fetch_ths_industry_members,
    normalize_eastmoney_research_reports,
    normalize_industry_members,
    normalize_mootdx_daily,
    normalize_tencent_daily,
    normalize_tencent_quote_snapshot,
    tencent_symbol,
)


def test_tencent_symbol_adds_exchange_prefix():
    assert tencent_symbol("000001") == "sz000001"
    assert tencent_symbol("300750") == "sz300750"
    assert tencent_symbol("600519") == "sh600519"
    assert tencent_symbol("510300") == "sh510300"


def test_normalize_tencent_daily_maps_akshare_columns():
    raw = pd.DataFrame([{
        "date": "2026-05-15",
        "open": "11.20",
        "close": "11.30",
        "high": "11.40",
        "low": "11.10",
        "amount": "123456",
    }])

    result = normalize_tencent_daily("1", raw)

    assert result.attrs["source_status"] == "ok"
    assert result.to_dict("records") == [{
        "trade_date": pd.Timestamp("2026-05-15"),
        "open": 11.2,
        "high": 11.4,
        "low": 11.1,
        "close": 11.3,
        "amount": 123456.0,
        "symbol": "000001",
        "country": "CN",
    }]


def test_normalize_tencent_quote_snapshot_extracts_valuation_fields():
    maotai_parts = [""] * 50
    maotai_parts[1] = "贵州茅台"
    maotai_parts[2] = "600519"
    maotai_parts[39] = "20.03"
    maotai_parts[45] = "16567.53"
    maotai_parts[46] = "6.12"
    raw = (
        'v_sz000001="51~平安银行~000001~10.86~10.99~10.96~856382~320514~535868~10.85~1352~'
        '10.84~2622~10.83~6442~10.82~12682~10.81~8811~10.86~1467~10.87~3006~10.88~3919~'
        '10.89~2815~10.90~2491~~20260518161406~-0.13~-1.18~10.97~10.82~10.86/856382/931697164~'
        '856382~93170~0.44~4.89~~10.97~10.82~1.36~2107.45~2107.48~0.45~12.09~9.89~0.90";\n'
        f'v_sh600519="{"~".join(maotai_parts)}";'
    )

    result = normalize_tencent_quote_snapshot(raw)

    assert result.attrs["source_status"] == "ok"
    assert result[["symbol", "name", "pe_ttm", "market_cap", "pb", "country"]].to_dict("records") == [
        {
            "symbol": "000001",
            "name": "平安银行",
            "pe_ttm": 4.89,
            "market_cap": 2107.48,
            "pb": 0.45,
            "country": "CN",
        },
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "pe_ttm": 20.03,
            "market_cap": 16567.53,
            "pb": 6.12,
            "country": "CN",
        },
    ]


def test_normalize_eastmoney_research_reports_keeps_forecast_fields():
    raw = pd.DataFrame([{
        "股票代码": "000001",
        "股票简称": "平安银行",
        "报告名称": "年报点评",
        "东财评级": "买入",
        "机构": "测试证券",
        "行业": "银行Ⅱ",
        "日期": "2026-04-26",
        "2026-盈利预测-收益": "2.08",
        "2026-盈利预测-市盈率": "5.3",
        "报告PDF链接": "https://example.com/report.pdf",
    }])

    result = normalize_eastmoney_research_reports("1", raw)

    assert result.attrs["source_status"] == "ok"
    assert result.to_dict("records") == [{
        "symbol": "000001",
        "name": "平安银行",
        "report_title": "年报点评",
        "rating": "买入",
        "institution": "测试证券",
        "industry": "银行Ⅱ",
        "report_date": pd.Timestamp("2026-04-26"),
        "eps_forecast_year_1": 2.08,
        "pe_forecast_year_1": 5.3,
        "source_url": "https://example.com/report.pdf",
    }]


def test_normalize_industry_members_maps_eastmoney_board_constituents():
    raw = pd.DataFrame([
        {"代码": "000001", "名称": "平安银行"},
        {"股票代码": "600519", "股票名称": "贵州茅台"},
    ])

    result = normalize_industry_members(raw, industry="银行", source="eastmoney_industry_board")

    assert result.attrs["source_status"] == "ok"
    assert result.to_dict("records") == [
        {"symbol": "000001", "name": "平安银行", "industry": "银行", "country": "CN", "source": "eastmoney_industry_board"},
        {"symbol": "600519", "name": "贵州茅台", "industry": "银行", "country": "CN", "source": "eastmoney_industry_board"},
    ]


def test_fetch_eastmoney_industry_members_aggregates_board_constituents():
    board_names = pd.DataFrame([
        {"板块名称": "银行"},
        {"板块名称": "白酒"},
    ])
    calls: list[str] = []

    def fake_cons(symbol: str) -> pd.DataFrame:
        calls.append(symbol)
        if symbol == "银行":
            return pd.DataFrame([{"代码": "000001", "名称": "平安银行"}])
        return pd.DataFrame([{"代码": "600519", "名称": "贵州茅台"}])

    result = fetch_eastmoney_industry_members(
        provider_names=lambda: board_names,
        provider_cons=fake_cons,
        sleep_seconds=0,
    )

    assert calls == ["银行", "白酒"]
    assert result[["symbol", "name", "industry"]].to_dict("records") == [
        {"symbol": "000001", "name": "平安银行", "industry": "银行"},
        {"symbol": "600519", "name": "贵州茅台", "industry": "白酒"},
    ]


def test_fetch_legulegu_sw_industry_members_uses_first_level_industry_pages():
    first_info = pd.DataFrame([
        {"行业代码": "801780.SI", "行业名称": "银行"},
        {"行业代码": "801120.SI", "行业名称": "食品饮料"},
    ])
    urls: list[str] = []

    def fake_requester(url: str, **_kwargs):
        urls.append(url)
        if "801780.SI" in url:
            text = """
            <table><tr><th>股票代码</th><th>股票简称</th><th>申万1级</th></tr>
            <tr><td>000001.SZ</td><td>平安银行</td><td>银行</td></tr></table>
            """
        else:
            text = """
            <table><tr><th>股票代码</th><th>股票简称</th><th>申万1级</th></tr>
            <tr><td>600519.SH</td><td>贵州茅台</td><td>食品饮料</td></tr></table>
            """
        return type("Resp", (), {"text": text})()

    result = fetch_legulegu_sw_industry_members(
        provider_first_info=lambda: first_info,
        requester=fake_requester,
        target_symbols=["600519"],
        sleep_seconds=0,
    )

    assert any("801780.SI" in url for url in urls)
    assert any("801120.SI" in url for url in urls)
    assert result[["symbol", "name", "industry"]].to_dict("records") == [
        {"symbol": "000001", "name": "平安银行", "industry": "银行"},
        {"symbol": "600519", "name": "贵州茅台", "industry": "食品饮料"},
    ]


def test_fetch_ths_industry_members_parses_detail_pages_until_targets_are_found():
    board_names = pd.DataFrame([
        {"name": "银行", "code": "881155"},
        {"name": "白酒", "code": "881273"},
    ])
    urls: list[str] = []

    def fake_requester(url: str, **_kwargs):
        urls.append(url)
        if "881155" in url:
            text = """
            <table><tr><th>代码</th><th>名称</th></tr>
            <tr><td>000001</td><td>平安银行</td></tr></table>
            """
        else:
            text = """
            <table><tr><th>代码</th><th>名称</th></tr>
            <tr><td>600519</td><td>贵州茅台</td></tr></table>
            """
        return type("Resp", (), {"text": text})()

    result = fetch_ths_industry_members(
        provider_names=lambda: board_names,
        requester=fake_requester,
        target_symbols=["600519"],
        sleep_seconds=0,
        max_pages=3,
    )

    assert any("881155" in url for url in urls)
    assert any("881273" in url for url in urls)
    assert result[["symbol", "name", "industry"]].to_dict("records") == [
        {"symbol": "000001", "name": "平安银行", "industry": "银行"},
        {"symbol": "600519", "name": "贵州茅台", "industry": "白酒"},
    ]


def test_fetch_ths_industry_members_parses_board_list_without_akshare_wrapper():
    def fake_requester(url: str, **_kwargs):
        if url == "http://q.10jqka.com.cn/thshy/":
            text = """
            <a href="http://q.10jqka.com.cn/thshy/detail/code/881155/">银行</a>
            """
        else:
            text = """
            <table><tr><th>代码</th><th>名称</th></tr>
            <tr><td>000001</td><td>平安银行</td></tr></table>
            """
        return type("Resp", (), {"text": text})()

    result = fetch_ths_industry_members(
        requester=fake_requester,
        target_symbols=["000001"],
        sleep_seconds=0,
        max_pages=1,
    )

    assert result[["symbol", "name", "industry"]].to_dict("records") == [
        {"symbol": "000001", "name": "平安银行", "industry": "银行"},
    ]


def test_fetch_cninfo_industry_members_prefers_sw_classification():
    calls: list[str] = []

    def fake_provider(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        calls.append(f"{symbol}:{start_date}:{end_date}")
        return pd.DataFrame([
            {
                "证券代码": symbol,
                "新证券简称": "用友网络",
                "分类标准": "中国上市公司协会上市公司行业分类标准",
                "行业门类": "信息传输、软件和信息技术服务业",
                "行业次类": None,
                "行业大类": "软件和信息技术服务业",
                "变更日期": pd.Timestamp("2024-02-08"),
            },
            {
                "证券代码": symbol,
                "新证券简称": "用友网络",
                "分类标准": "申银万国行业分类标准",
                "行业门类": "计算机",
                "行业次类": "软件开发",
                "行业大类": "横向通用软件",
                "变更日期": pd.Timestamp("2021-07-30"),
            },
        ])

    result = fetch_cninfo_industry_members(
        ["600588"],
        provider=fake_provider,
        end_date="20260522",
        sleep_seconds=0,
    )

    assert calls == ["600588:20200101:20260522"]
    assert result[["symbol", "name", "industry", "source"]].to_dict("records") == [
        {
            "symbol": "600588",
            "name": "用友网络",
            "industry": "软件开发",
            "source": "cninfo_industry_change",
        },
    ]


def test_normalize_mootdx_daily_accepts_vol_column():
    raw = pd.DataFrame([{
        "date": "2026-05-15",
        "open": 10,
        "high": 11,
        "low": 9,
        "close": 10.5,
        "vol": 1000,
        "amount": 2000,
    }])

    result = normalize_mootdx_daily("600519", raw)

    assert result.attrs["source_status"] == "ok"
    assert result.iloc[0].to_dict() == {
        "trade_date": pd.Timestamp("2026-05-15"),
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "volume": 1000.0,
        "amount": 2000.0,
        "symbol": "600519",
        "country": "CN",
    }


def test_normalize_mootdx_daily_accepts_datetime_index():
    raw = pd.DataFrame(
        [{"open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000}],
        index=pd.DatetimeIndex([pd.Timestamp("2026-05-15")], name="datetime"),
    )

    result = normalize_mootdx_daily("600519", raw)

    assert result.attrs["source_status"] == "ok"
    assert result.iloc[0]["trade_date"] == pd.Timestamp("2026-05-15")


def test_normalize_mootdx_daily_accepts_unnamed_datetime_index():
    raw = pd.DataFrame(
        [{"open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000}],
        index=pd.DatetimeIndex([pd.Timestamp("2026-05-15")]),
    )

    result = normalize_mootdx_daily("600519", raw)

    assert result.attrs["source_status"] == "ok"
    assert result.iloc[0]["trade_date"] == pd.Timestamp("2026-05-15")
