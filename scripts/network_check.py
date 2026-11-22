"""Claude API 网络连通性自检工具。

连续探测 https://api.anthropic.com 多次，报告每次结果（成功 / 403 被拒 / 超时断连），
帮助判断当前网络出口是否被 Anthropic（Cloudflare 网关）拒绝。

用法：
    .venv/Scripts/python scripts/network_check.py        # 默认测 5 次
    .venv/Scripts/python scripts/network_check.py 10     # 测 10 次
"""
from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 允许直接从 scripts/ 目录运行：将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 控制台默认 GBK 编码，中文输出会乱码，强制 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from utils import config  # noqa: E402


def probe() -> tuple[str, float]:
    """探测一次 API 连通性。

    出参:
        (状态描述, 耗时秒)：状态为 OK(200) / HTTP 403 / HTTP 其他 / 连接失败(异常类型)。
    """
    if not config.ANTHROPIC_API_KEY:
        return "未配置 API Key", 0.0
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models",
        headers={
            "x-api-key": config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return f"OK({resp.status})", time.time() - t0
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}（被拒）", time.time() - t0
    except Exception as e:  # noqa: BLE001
        return f"连接失败（{type(e).__name__}）", time.time() - t0


def main() -> int:
    """运行检测。出参: 进程退出码。"""
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"开始探测 Claude API 连通性（{n} 次）…\n")

    ok = 0
    for i in range(1, n + 1):
        status, dt = probe()
        if status.startswith("OK"):
            ok += 1
        print(f"第{i}次: {status}  耗时 {dt:.1f}s")

    print(f"\n结果：{ok}/{n} 次成功")
    if ok == 0:
        print("结论：当前网络出口被 Anthropic（Cloudflare 网关）全部拒绝。")
        print("      这不是代码问题。请更换代理节点/网络（避开数据中心 IP）后重试。")
    elif ok < n:
        print("结论：网络不稳定（间歇性被拒）。使用时若报 403/超时，多试几次或换节点。")
    else:
        print("结论：网络正常，可以直接使用。")
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
