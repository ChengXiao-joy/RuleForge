"""全局配置模块。

从环境变量 / 项目根目录 .env 文件读取配置，并向各层提供统一入口。
所有 LLM 调用参数、存储路径、日志参数均在此集中管理，避免散落魔法值。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（utils 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent

# 加载 .env（如不存在则忽略，允许纯环境变量方式启动）
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Claude API
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
# 自定义 API 端点（可选）：默认直连官方 https://api.anthropic.com。
# 网络无法直连时，可指向兼容 Anthropic 协议的中转/代理端点（注意密钥安全）。
ANTHROPIC_BASE_URL = os.getenv("ANTHROPIC_BASE_URL", "").strip() or None
# 默认模型：Claude 官方当前默认 Opus 5.5（未显式指定时使用）
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5")
# 单次输出 token 上限（规则树 JSON 通常几百 token，留足余量）
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "8192"))
# 思考深度：low|medium|high|xhigh|max
LLM_EFFORT = os.getenv("LLM_EFFORT", "medium")
# 单次请求超时（秒）；网络不稳时建议调小，避免页面一直转圈
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))
# SDK 网络层自动重试次数（0/1/2）；网络不稳时调小可更快暴露失败
LLM_SDK_MAX_RETRIES = int(os.getenv("LLM_SDK_MAX_RETRIES", "1"))
# 自然语言解析时 JSON 格式错误的最多重试次数（阶段0 要求为 2）
JSON_MAX_RETRIES = int(os.getenv("JSON_MAX_RETRIES", "2"))

# ---------------------------------------------------------------------------
# 本地存储
# ---------------------------------------------------------------------------
DB_PATH = BASE_DIR / os.getenv("DB_PATH", "storage/ruleforge.db")

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
LOG_DIR = BASE_DIR / os.getenv("LOG_DIR", "logs")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def has_api_key() -> bool:
    """判断是否已配置 Claude API Key。

    出参:
        bool: True 表示已配置，False 表示未配置（解析功能不可用）。
    """
    return bool(ANTHROPIC_API_KEY)
