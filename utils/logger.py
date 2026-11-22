"""日志模块。

统一初始化根 logger，同时输出到控制台与滚动日志文件。
幂等：多次调用 setup_logging 不会重复添加 handler。
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from utils import config

_initialized = False


def _ensure_utf8_stdout() -> None:
    """强制标准输出/错误为 UTF-8。

    Windows 控制台默认编码可能是 GBK(cp936)，直接 print 中文或 emoji 会触发
    UnicodeEncodeError 崩溃。这里将 stdout/stderr 重配置为 UTF-8，避免该问题。
    出参: None。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass  # 部分非控制台流不支持 reconfigure，忽略即可


def setup_logging(level: str | None = None, log_file: Path | None = None) -> logging.Logger:
    """初始化日志系统（幂等）。

    入参:
        level:    日志级别字符串（DEBUG/INFO/WARNING/ERROR），为空时使用配置值。
        log_file: 日志文件路径，为空时使用配置的 LOG_DIR/ruleforge.log。

    出参:
        logging.Logger: 应用根 logger（名称为 ruleforge）。
    """
    global _initialized
    logger = logging.getLogger("ruleforge")
    if _initialized:
        return logger

    _ensure_utf8_stdout()

    lvl = (level or config.LOG_LEVEL).upper()
    logger.setLevel(getattr(logging, lvl, logging.INFO))
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s %(filename)s:%(lineno)d - %(message)s"
    )

    # 控制台输出
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # 滚动文件输出（单文件最大 5MB，保留 3 份）
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_file or config.LOG_DIR / "ruleforge.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    logger.propagate = False
    _initialized = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """获取命名 logger。

    入参:
        name: 子模块名（如 "llm.parser"），为空时返回根 logger。

    出参:
        logging.Logger: 对应的 logger。
    """
    setup_logging()
    if name:
        return logging.getLogger(f"ruleforge.{name}")
    return logging.getLogger("ruleforge")
