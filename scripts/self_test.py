"""RuleForge 自动化自测脚本（阶段5 收尾，任务5）。

自动执行完整核心链路（全程使用独立临时数据库，不污染正式数据）：
    1. 新增规则（含初始版本快照）
    2. 单规则自检（正常规则通过）
    3. 单规则自检（坏规则识别）
    4. 编辑规则（版本递增 + 快照生成）
    5. 沙箱仿真（命中链路 + 最终结论）
    6. JSON 导出（含版本快照）
    7. JSON 导入（新库往返一致 + 坏数据拦截）

运行结束输出结构化自测报告，标记每项成功/失败，全部通过时退出码为 0。

用法：
    .venv\Scripts\python scripts\self_test.py      （或双击「shortcuts」→「自测.bat」）
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

# 允许从任意目录运行：将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 控制台 UTF-8 输出（Windows GBK 兼容）
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rule_engine import schema  # noqa: E402
from rule_engine.sandbox_simulator import simulate  # noqa: E402
from rule_engine.single_rule_checker import check_rule  # noqa: E402
from storage.db import Database  # noqa: E402
from utils.rule_import_export import export_rules_json, import_rules_json  # noqa: E402

# 自测报告：每项 {"step": 步骤名, "status": PASS/FAIL, "detail": 补充信息}
REPORT: list[dict] = []


def record(step: str, ok: bool, detail: str = "") -> None:
    """记录一项自测结果。

    入参: step 步骤名；ok 是否通过；detail 补充信息。出参: None。
    """
    REPORT.append({"step": step, "status": "PASS" if ok else "FAIL", "detail": detail})


def _rule(rid: str, name: str, value, priority: int = 50) -> schema.Rule:
    """构造自测规则。出参: Rule。"""
    return schema.Rule(
        rule_id=rid,
        name=name,
        priority=priority,
        conditions=schema.ConditionLeaf(field="order.amount", operator="gt", value=value),
        actions=[schema.Action(action_type="notify", target="t")],
    )


def main() -> int:
    """自测主流程。出参: 进程退出码（0 = 全部通过）。"""
    tmp = Path(tempfile.mkdtemp(prefix="ruleforge_selftest_"))
    db = Database(tmp / "main.db")
    print(f"自测使用独立临时库（不污染正式数据）：{db.db_path}")
    print()

    try:
        # 1. 新增规则（含初始版本快照）
        db.create_rule(_rule("R1", "自测规则", 1000))
        ok = db.get_rule("R1") is not None and len(db.list_rule_versions("R1")) == 1
        record("1. 新增规则（含初始版本快照）", ok)

        # 2. 单规则自检：正常规则通过
        ok = check_rule(db.get_rule("R1")).ok
        record("2. 单规则自检（正常规则通过）", ok)

        # 3. 单规则自检：坏规则识别
        bad = schema.Rule(
            rule_id="BAD",
            name="坏规则",
            conditions=schema.ConditionLeaf(field="金额", operator="gt", value=1),
            actions=[schema.Action(action_type="notify", target="t")],
        )
        ok = not check_rule(bad).ok
        record("3. 单规则自检（坏规则识别）", ok)

        # 4. 编辑规则：版本递增 + 自动快照
        updated = db.update_rule(_rule("R1", "自测规则v2", 5000, 80), note="自测修改")
        versions = db.list_rule_versions("R1")
        ok = updated is not None and updated.version == 2 and len(versions) == 2
        record(
            "4. 编辑规则（版本递增 + 快照生成）",
            ok,
            f"当前 v{updated.version if updated else '?'}，快照 {len(versions)} 条",
        )

        # 5. 沙箱仿真：命中链路 + 最终结论
        res = simulate({"order.amount": 6000}, [db.get_rule("R1")])
        ok = res.hit_count == 1 and bool(res.final_actions)
        record("5. 沙箱仿真（命中链路 + 最终结论）", ok, f"命中 {res.hit_count} 条")

        # 6. JSON 导出（含版本快照）
        text = export_rules_json(db)
        data = json.loads(text)
        ok = len(data["rules"]) == 1 and len(data["rules"][0]["versions"]) == 2
        record("6. JSON 导出（含版本快照）", ok)

        # 7. JSON 导入：新库往返一致 + 坏数据拦截
        dst = Database(tmp / "dst.db")
        r = import_rules_json(dst, text)
        ok = (
            r.success == 1
            and dst.get_rule("R1").version == 2
            and len(dst.list_rule_versions("R1")) == 2
        )
        record("7. JSON 导入（新库往返一致）", ok)

        bad_payload = json.dumps(
            {"rules": [{"rule": bad.model_dump(), "versions": []}]}, ensure_ascii=False
        )
        rb = import_rules_json(dst, bad_payload)
        ok = rb.success == 0 and rb.failed == 1 and dst.get_rule("BAD") is None
        record("8. JSON 导入（坏数据拦截不落库）", ok)
    except Exception:  # noqa: BLE001
        record("（异常中断）", False, traceback.format_exc(limit=2))

    # 输出结构化自测报告
    print()
    print("=" * 56)
    print("  RuleForge 自动化自测报告")
    print("=" * 56)
    for r in REPORT:
        mark = "[PASS]" if r["status"] == "PASS" else "[FAIL]"
        line = f"{mark} {r['step']}"
        if r["detail"]:
            line += f"　({r['detail']})"
        print(line)
    passed = sum(1 for r in REPORT if r["status"] == "PASS")
    total = len(REPORT)
    print("-" * 56)
    print(f"结果：{passed}/{total} 通过")
    print("=" * 56)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
