"""pytest 根配置：确保项目根目录在 sys.path 上，使测试可 import 各层模块。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
