"""RuleForge 一键本地启动脚本（供「run.bat」调用）。

流程（全程幂等，可重复运行）：
    1. 检查/创建虚拟环境 .venv；
    2. 检查/安装依赖（requirements.txt 锁定版本）；
    3. 检查 .env 配置（缺失时从 .env.example 复制并提示填写 API Key）；
    4. 启动 Streamlit 页面。

只用 Python 标准库，不依赖任何第三方包（虚拟环境缺失时用系统 Python 也能跑）。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# 项目根目录（scripts 的上一级）
ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str]) -> int:
    """执行子进程命令并返回退出码。

    入参:
        cmd: 命令与参数列表。

    出参:
        int: 退出码。
    """
    return subprocess.run(cmd, cwd=ROOT, check=False).returncode


def main() -> int:
    """一键启动主流程。出参: 进程退出码。"""
    print("=" * 56)
    print("  RuleForge 一键本地启动")
    print("=" * 56)

    venv_py = ROOT / ".venv" / "Scripts" / "python.exe"

    # 1) 虚拟环境
    if not venv_py.exists():
        print("[1/4] 首次运行：正在创建虚拟环境…")
        if _run([sys.executable, "-m", "venv", str(ROOT / ".venv")]) != 0:
            print("[错误] 虚拟环境创建失败。请确认已安装 Python 3.10+。")
            return 1
    py = str(venv_py)

    # 2) 依赖
    try:
        deps_ok = _run([py, "-c", "import anthropic, streamlit, pydantic, dotenv"]) == 0
    except OSError:
        deps_ok = False
    if not deps_ok:
        print("[2/4] 正在安装依赖（首次较慢，请耐心等待）…")
        if _run([py, "-m", "pip", "install", "-r", "requirements.txt"]) != 0:
            print("[错误] 依赖安装失败，请检查网络后重试。")
            return 1
    else:
        print("[2/4] 依赖已就绪，跳过安装。")

    # 3) .env 配置
    env_file = ROOT / ".env"
    if not env_file.exists():
        shutil.copy(ROOT / ".env.example", env_file)
        print("[3/4] 已生成 .env 模板文件（项目根目录）。")
        print("      请打开 .env，填入 ANTHROPIC_API_KEY 后重新运行本脚本。")
        print("      （.env 已被 gitignore 忽略，密钥不会提交到 Git。）")
        return 0

    # 4) 启动
    print("[4/4] 正在启动网页，浏览器将自动打开（Ctrl+C 可停止）…")
    return _run([py, "-m", "streamlit", "run", "ui/app.py"])


if __name__ == "__main__":
    sys.exit(main())
