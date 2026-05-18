"""Runtime network setup for local finance data fetchers."""
from __future__ import annotations

import os


def prepare_finance_data_environment() -> None:
    """Avoid local proxy interception for public market-data endpoints."""
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        os.environ.pop(key, None)
    no_proxy_domains = [
        "eastmoney.com",
        "push2.eastmoney.com",
        "82.push2.eastmoney.com",
        "push2his.eastmoney.com",
        "gu.qq.com",
        "q.10jqka.com.cn",
        "yahoo.com",
    ]
    current = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    merged = [item.strip() for item in current.split(",") if item.strip()]
    for domain in no_proxy_domains:
        if domain not in merged:
            merged.append(domain)
    value = ",".join(merged)
    os.environ["NO_PROXY"] = value
    os.environ["no_proxy"] = value
    try:
        import requests.utils

        requests.utils.getproxies = lambda: {}
    except Exception:
        pass
