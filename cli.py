"""RuleForge 命令行入口。

用法示例：
    python cli.py parse "如果员工本月迟到超过3次，就发邮件提醒人事部门"
    python cli.py parse "如果订单金额超过1000元且客户是VIP，就批准打8折" --save
    python cli.py list
    python cli.py selftest
"""
from __future__ import annotations

import argparse
import json

from llm_layer import parser as rule_parser
from llm_layer.parser import extract_json
from rule_engine import schema
from rule_engine.schema import gen_rule_id, now_iso
from rule_engine.single_rule_checker import check_rule
from storage.db import get_db


def cmd_parse(args: argparse.Namespace) -> None:
    """解析单条自然语言为规则。"""
    result = rule_parser.parse_natural_language(args.text)

    if result.success and result.rule is not None:
        rule = result.rule
        if not rule.rule_id:
            rule.rule_id = gen_rule_id()
        if not rule.created_at:
            rule.created_at = rule.updated_at = now_iso()

        print(f"[成功] 第 {result.attempts} 次尝试解析成功（{'一次通过' if result.first_attempt_valid else '经过重试'}）：")
        print(rule.model_dump_json(indent=2))
        if args.save:
            get_db().upsert_rule(rule)
            print(f"已保存规则 {rule.rule_id}")

    elif result.success and result.needs_clarification:
        print(f"[需要澄清] {result.clarification_message}")
        for q in result.questions:
            print(f"  - {q}")

    else:
        print(f"[失败] {result.error}")
        for i, raw in enumerate(result.raw_outputs, 1):
            print(f"--- 第 {i} 次原始输出 ---\n{raw}")


def cmd_list(args: argparse.Namespace) -> None:
    """列出已保存规则。"""
    rows = get_db().list_rules()
    if not rows:
        print("暂无已保存规则。")
        return
    for r in rows:
        print(
            f"{r['rule_id']}  优先级 {r['priority']:>3}  {r['status']:<8} "
            f"{r['category']:<6} {r['name']}"
        )


def cmd_scan(args: argparse.Namespace) -> None:
    """批量冲突扫描：读取全库规则，过滤坏规则后两两比对，结果落库。"""
    from rule_engine.conflict_detector import scan_rules

    db = get_db()
    result = scan_rules(db.get_all_rules())
    skipped = [(b.rule.rule_id, "单规则自检未通过") for b in result.bad_rules]
    db.save_scan_result(result.conflicts, skipped)

    print(
        f"扫描完成：共 {result.total_rules} 条规则，参与比对 {result.scanned_rules} 条，"
        f"过滤坏规则 {len(result.bad_rules)} 条，发现 {len(result.conflicts)} 个冲突"
    )
    for b in result.bad_rules:
        print(f"[坏规则] {b.rule.rule_id}（{b.rule.name}）")
    for c in result.conflicts:
        print(f"[{c.severity}] {c.conflict_type}  {c.rule_a_id} <-> {c.rule_b_id}")
        print(f"    {c.description}")


def cmd_export(args: argparse.Namespace) -> None:
    """批量导出规则（全部或选中）为 JSON。"""
    from pathlib import Path

    from utils.rule_import_export import export_rules_json

    ids = [x.strip() for x in args.ids.split(",")] if args.ids else None
    text = export_rules_json(get_db(), ids)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"已导出到 {args.out}（共 {len(json.loads(text)['rules'])} 条规则）")
    else:
        print(text)


def cmd_import(args: argparse.Namespace) -> None:
    """批量导入规则 JSON 文件（自动单规则自检 + 导入报告）。"""
    from pathlib import Path

    from utils.rule_import_export import import_rules_json

    text = Path(args.file).read_text(encoding="utf-8")
    result = import_rules_json(get_db(), text)
    print(result.summary_text())


def cmd_stats(args: argparse.Namespace) -> None:
    """输出数据看板统计。"""
    stats = get_db().dashboard_stats()
    print(f"规则总数：{stats['rules']}")
    print(f"自检异常规则数：{stats['bad_rules']}")
    print(f"冲突对数量：{stats['conflicts']}")
    print(f"版本快照总数：{stats['versions']}")
    print(f"沙箱推演次数：{stats['simulations']}")


def cmd_check(args: argparse.Namespace) -> None:
    """对已保存规则做单条自检。"""
    rule = get_db().get_rule(args.rule_id)
    if rule is None:
        print(f"规则 {args.rule_id} 不存在。")
        return
    check = check_rule(rule)
    print(f"规则 {rule.rule_id}（{rule.name}）单条自检：")
    if not check.issues:
        print("[通过] 未发现内部缺陷")
    else:
        for i in check.issues:
            mark = "[错误]" if i.severity == "error" else "[警告]"
            loc = f"（{i.path}）" if i.path else ""
            print(f"{mark} [{i.code}] {i.message}{loc}")


def cmd_selftest(args: argparse.Namespace) -> None:
    """运行离线自检（不依赖 API Key）：Schema、JSON 提取、SQLite 读写。"""
    checks: list[tuple[str, bool, str]] = []

    # 1. Schema 校验：合法规则应通过
    try:
        rule = schema.Rule(
            rule_id=gen_rule_id(),
            name="迟到提醒",
            description="测试规则",
            category="人事",
            effective_scope=schema.Scope(target_type="employee"),
            conditions=schema.ConditionLeaf(field="attendance.late_count", operator="gt", value=3),
            actions=[schema.Action(action_type="notify", target="人事", channel="email")],
        )
        checks.append(("Schema 合法规则校验", True, "通过"))
    except Exception as e:  # noqa: BLE001
        checks.append(("Schema 合法规则校验", False, str(e)))

    # 2. Schema 校验：非法字段应被拒绝（extra=forbid）
    bad = False
    try:
        schema.ConditionLeaf(field="x", operator="gt", value=1, unknown_field=1)
    except Exception:  # noqa: BLE001
        bad = True
    checks.append(("Schema 拒绝未定义字段", bad, "通过" if bad else "未拒绝"))

    # 3. JSON 提取：三种形态
    e1 = extract_json('{"a": 1}') is not None
    e2 = extract_json('```json\n{"a": 1}\n```') is not None
    e3 = extract_json('说明文字 {"a": 1} 结尾') is not None
    e4 = extract_json("没有json") is None
    ok = e1 and e2 and e3 and e4
    checks.append(("JSON 提取（直接/围栏/夹杂/无）", ok, "通过" if ok else "失败"))

    # 4. SQLite 读写
    import tempfile
    from pathlib import Path

    try:
        tmp = Path(tempfile.mkdtemp()) / "t.db"
        from storage.db import Database

        d = Database(tmp)
        rid = d.upsert_rule(rule)
        got = d.get_rule(rid)
        ok_db = got is not None and got.name == "迟到提醒"
        ok_db = ok_db and len(d.list_rules()) == 1 and d.delete_rule(rid) and d.get_rule(rid) is None
        checks.append(("SQLite 读写（增查删）", ok_db, "通过" if ok_db else "失败"))
    except Exception as e:  # noqa: BLE001
        checks.append(("SQLite 读写（增查删）", False, str(e)))

    # 汇总
    print("=== RuleForge 离线自检 ===")
    all_ok = True
    for name, passed, detail in checks:
        mark = "✅" if passed else "❌"
        all_ok = all_ok and passed
        print(f"{mark} {name}: {detail}")
    print("总结：" + ("全部通过，本地环境就绪。" if all_ok else "存在失败项，请检查。"))
    print("（如需验证 Claude API 调用，请配置 ANTHROPIC_API_KEY 后运行 `python cli.py parse \"...\"`）")


def main() -> None:
    """命令行入口。出参: None。"""
    p = argparse.ArgumentParser(prog="ruleforge", description="RuleForge 规则工坊")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("parse", help="解析自然语言为规则")
    pp.add_argument("text", help="自然语言文本")
    pp.add_argument("--save", action="store_true", help="解析成功后保存到数据库")
    pp.set_defaults(func=cmd_parse)

    pl = sub.add_parser("list", help="列出已保存规则")
    pl.set_defaults(func=cmd_list)

    pc = sub.add_parser("check", help="对已保存规则做单条自检")
    pc.add_argument("rule_id", help="规则 ID")
    pc.set_defaults(func=cmd_check)

    ps2 = sub.add_parser("scan", help="批量冲突扫描（全库规则两两比对并落库）")
    ps2.set_defaults(func=cmd_scan)

    pe = sub.add_parser("export", help="批量导出规则为 JSON")
    pe.add_argument("--ids", help="逗号分隔的规则ID列表；不传表示导出全部")
    pe.add_argument("--out", help="输出文件路径；不传则打印到屏幕")
    pe.set_defaults(func=cmd_export)

    pi = sub.add_parser("import", help="批量导入规则 JSON 文件")
    pi.add_argument("file", help="JSON 文件路径")
    pi.set_defaults(func=cmd_import)

    pst = sub.add_parser("stats", help="数据看板统计")
    pst.set_defaults(func=cmd_stats)

    ps = sub.add_parser("selftest", help="运行离线自检（无需 API Key）")
    ps.set_defaults(func=cmd_selftest)

    args = p.parse_args()
    # 全局异常兜底：命令执行出错时友好提示，不打印原始堆栈
    try:
        args.func(args)
    except Exception as e:  # noqa: BLE001
        from utils.logger import get_logger

        get_logger("cli").exception("命令执行异常")
        print(f"❌ 执行出错：{e}（详细错误已记录到 logs/ruleforge.log）")


if __name__ == "__main__":
    main()
