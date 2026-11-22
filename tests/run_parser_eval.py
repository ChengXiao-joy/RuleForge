"""阶段0 验收评测脚本。

对 tests/test_cases.json 中的 10 条验收样例调用【真实 Claude API】，
统计「一次输出合法 JSON」的比例（验收标准：>= 8/10）。

运行前提：已配置 ANTHROPIC_API_KEY。
用法：
    python tests/run_parser_eval.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 允许直接从 tests/ 目录运行本脚本：将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm_layer.parser import parse_natural_language
from storage.db import get_db


def main() -> int:
    """运行评测。出参: 进程退出码（0 表示达标）。"""
    cases_file = Path(__file__).parent / "test_cases.json"
    cases = json.loads(cases_file.read_text(encoding="utf-8"))

    # 仅取验收样例（排除冲突样例）
    eval_cases = [c for c in cases if c.get("eval", True) and c.get("type") != "conflict"]
    db = get_db()

    print(f"开始评测：共 {len(eval_cases)} 条验收样例（模型将逐条调用真实 Claude API）\n")
    print(f"{'ID':<5} {'场景':<6} {'类型':<6} {'期望':<16} {'实际':<16} {'结果':<6} 尝试次数")
    print("-" * 80)

    first_attempt_ok = 0
    total = len(eval_cases)

    for c in eval_cases:
        r = parse_natural_language(c["input"])

        if r.success and r.rule is not None:
            got_type = "rule"
        elif r.success and r.needs_clarification:
            got_type = "clarification"
        else:
            got_type = "fail"

        first_ok = r.first_attempt_valid
        if first_ok:
            first_attempt_ok += 1

        # 期望类型是否匹配（二次质量指标，不计入 ≥8 验收）
        expected = c["expected_result_type"]
        matched = got_type == expected

        db.log_parse(c["input"], r.success, r.attempts, got_type, r.error)

        mark = "✅" if first_ok else "❌"
        print(
            f"{c['id']:<5} {c['scenario']:<6} {c['type']:<6} {expected:<16} "
            f"{got_type:<16} {mark:<6} {r.attempts if r.attempts else '-'}"
        )
        if not matched and expected != "clarification":
            print(f"      ⚠️ 期望 {expected} 但实际 {got_type}（不影响一次通过验收，仅提示）")

    print("-" * 80)
    print(f"一次输出合法 JSON：{first_attempt_ok}/{total}")
    passed = first_attempt_ok >= 8
    print("验收标准（>= 8/10）：" + ("✅ 通过" if passed else "❌ 未通过"))

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
