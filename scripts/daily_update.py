#!/usr/bin/env python3
"""每日收盘流程 wrapper：委托 daily_close.sh 完成全链路更新。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DAILY_CLOSE = PROJECT / "scripts" / "daily_close.sh"


def _prepare_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env.pop(key, None)
    env["no_proxy"] = "*"
    return env


def _filtered_tail(output: str, limit: int = 80) -> str:
    lines = [
        line for line in output.splitlines()
        if "possibly delisted" not in line and "yfinance CN daily empty" not in line
    ]
    return "\n".join(lines[-limit:])


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} 开始每日收盘流程 ===")
    result = subprocess.run(
        ["bash", str(DAILY_CLOSE), *argv],
        cwd=str(PROJECT),
        env=_prepare_env(),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    output = _filtered_tail((result.stdout or "") + (result.stderr or ""))
    print(output if output else "(无输出)")
    if result.returncode != 0:
        print(f"退出码: {result.returncode}")
    print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} 结束 ===")
    return int(result.returncode)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
