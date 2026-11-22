"""一键命令行验收脚本（供「一键验收.bat」双击调用）。

依次执行：离线自检 → 自然语言解析（正常规则）→ 自然语言解析（缺陷输入）
→ 规则列表 → 看板统计，输出清晰的验收结果。

说明：所有中文内容都放在本 Python 文件内（UTF-8），批处理文件保持纯 ASCII，
避免 cmd 控制台中文编码乱码/解析断裂问题。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许从任意目录运行：将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 控制台 UTF-8 输出（Windows GBK 兼容）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from llm_layer import parser as rule_parser  # noqa: E402
from cli import cmd_list, cmd_selftest, cmd_stats  # noqa: E402


def _section(title: str) -> None:
    """打印分隔标题。入参: title 标题文本。出参: None。"""
    print()
    print("=" * 60)
    print(title)
    print("=" * 60)


def run_parse_demo(text: str) -> None:
    """执行一次自然语言解析并打印结果摘要。

    入参:
        text: 自然语言文本。

    出参: None。
    """
    result = rule_parser.parse_natural_language(text)
    if result.success and result.rule is not None:
        print(
            f"[成功] 第 {result.attempts} 次尝试解析成功"
            f"（{'一次通过' if result.first_attempt_valid else '经过重试'}）"
        )
        print(f"  规则名：{result.rule.name}")
        print(f"  条件：{result.rule.conditions.model_dump()}")
        print(f"  动作数：{len(result.rule.actions)}")
    elif result.success and result.needs_clarification:
        print(f"[需要澄清] {result.clarification_message}")
        for q in result.questions:
            print(f"  - {q}")
    else:
        print(f"[失败] {result.error}")


def main() -> None:
    """验收主流程。出参: None。"""
    _section("[1/5] 离线自检（Schema / JSON / SQLite）")
    cmd_selftest(None)

    _section("[2/5] 自然语言解析：正常规则（应解析成功）")
    run_parse_demo("如果员工本月迟到超过3次，就发邮件提醒人事部门")

    _section("[3/5] 自然语言解析：缺陷输入（应返回需要澄清）")
    run_parse_demo("处理一下")

    _section("[4/5] 规则列表")
    cmd_list(None)

    _section("[5/5] 看板统计")
    cmd_stats(None)

    print()
    print("=" * 60)
    print("全部执行完毕（解析部分需已配置 API Key）")
    print("=" * 60)


if __name__ == "__main__":
    main()
