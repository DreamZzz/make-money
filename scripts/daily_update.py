#!/opt/homebrew/bin/python3.12
"""每日数据更新 wrapper：停 Dashboard → 跑更新 → 启 Dashboard"""
import os
import subprocess
import sys
import time

PROJECT = "/Users/zhaoqiang/Documents/Project/make-money"
DASHBOARD_PLIST = os.path.expanduser("~/Library/LaunchAgents/com.quant.dashboard.plist")


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} 开始每日数据更新 ===")

# 1. 暂停 Dashboard
print("停止 Dashboard...")
run(f"launchctl unload {DASHBOARD_PLIST}")
time.sleep(3)

# 2. 跑更新
print("拉取数据...")
os.chdir(PROJECT)
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)
os.environ["no_proxy"] = "*"

result = subprocess.run(
    ["/opt/homebrew/bin/python3.12", "-m", "src.data_pipeline.main", "update"],
    cwd=PROJECT,
    capture_output=True, text=True, timeout=300,
)
output = (result.stdout + result.stderr).strip()
if output:
    # 只打印关键行，去掉 yfinance 的 "possibly delisted" 噪音
    lines = [l for l in output.split("\n") if "possibly delisted" not in l and "yfinance CN daily empty" not in l]
    print("\n".join(lines[-50:]))
else:
    print("(无输出)")
if result.returncode != 0:
    print(f"退出码: {result.returncode}")

# 3. 重启 Dashboard
print("启动 Dashboard...")
run(f"launchctl load {DASHBOARD_PLIST}")
print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} 结束 ===")
