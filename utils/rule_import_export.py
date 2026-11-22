"""规则批量导入导出（utils/rule_import_export.py，阶段5）。

职责：
- 批量导出全部/选中规则为 JSON（含版本快照基础信息）；
- 批量导入 JSON 文件：导入前自动执行单规则自检，不合格规则拦截、不强行写入坏数据；
- 输出导入报告（成功条数、失败条数、失败原因清单）。

红线：所有校验、统计、拦截逻辑全部自研确定性代码；LLM 不参与导入导出校验，
最多用于报告文案润色（本模块不调用 LLM）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import ValidationError

from rule_engine.schema import Rule, now_iso
from rule_engine.single_rule_checker import check_rule


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------
def export_rules_json(db, rule_ids: list[str] | None = None) -> str:
    """批量导出规则为 JSON 字符串（含版本快照基础信息）。

    入参:
        db:       Database 实例（只读）。
        rule_ids: 要导出的规则 ID 列表；为空/None 表示导出全部。

    出参:
        str: 导出 JSON 字符串，结构：
        {"app": "RuleForge", "exported_at": "...", "rules": [
            {"rule": {完整规则}, "versions": [
                {"version": 1, "note": "...", "tag": "...", "created_at": "...",
                 "after_json": {该版本完整规则}}
            ]}
        ]}
    """
    if rule_ids:
        rules = [r for r in (db.get_rule(i) for i in rule_ids) if r is not None]
    else:
        rules = db.get_all_rules()

    payload = {"app": "RuleForge", "exported_at": now_iso(), "rules": []}
    for r in rules:
        versions = db.list_rule_versions(r.rule_id, include_archived=True)
        versions_basic = [
            {
                "version": v["version"],
                "note": v["note"],
                "tag": v["tag"],
                "created_at": v["created_at"],
                "after_json": json.loads(v["after_json"]),
            }
            for v in sorted(versions, key=lambda x: x["version"])
        ]
        payload["rules"].append({"rule": r.model_dump(), "versions": versions_basic})
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 导入
# ---------------------------------------------------------------------------
@dataclass
class ImportResult:
    """导入报告。

    字段:
        total:    待导入条目总数。
        success:  成功条数。
        failed:   失败条数。
        failures: 失败清单，每项 {"index"/"rule_id"/"name", "reason"}。
    """
    total: int = 0
    success: int = 0
    failed: int = 0
    failures: list[dict] = field(default_factory=list)

    def summary_text(self) -> str:
        """生成报告摘要文本。出参: str。"""
        lines = [f"导入完成：共 {self.total} 条，成功 {self.success} 条，失败 {self.failed} 条。"]
        for f in self.failures:
            lines.append(f"  ✗ {f['name']}（{f['rule_id']}）：{f['reason']}")
        return "\n".join(lines)


def _check_fail_reason(chk) -> str:
    """自检失败的友好原因摘要（取前3个错误）。入参: 自检结果。出参: str。"""
    errs = chk.errors
    if not errs:
        return "单规则自检未通过（存在告警）"
    return f"单规则自检未通过：{'；'.join(f'[{i.code}] {i.message}' for i in errs[:3])}"


def _parse_items(json_text: str):
    """解析导入 JSON 为条目列表。

    入参:
        json_text: 导入的 JSON 文本。

    出参:
        (list, str | None): (条目列表, 错误信息)。错误时条目列表为空。
    """
    try:
        data = json.loads(json_text)
    except (json.JSONDecodeError, ValueError) as e:
        return [], f"JSON 解析失败：{e}"
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict) and isinstance(data.get("rules"), list):
        return data["rules"], None
    return [], "格式不正确：应为 {\"rules\": [...]} 或规则数组"


def _classify_item(db, item):
    """校验单个导入条目（不写入），返回分类结果。

    入参:
        db:   Database 实例（只读）。
        item: 导入条目（{"rule": ..., "versions": [...]} 或直接是规则 dict）。

    出参:
        (status, rule, reason):
        status = "ok"（可导入）/ "duplicate"（ID 已存在）/ "invalid"（校验失败）。
    """
    rule_dict = item.get("rule") if isinstance(item, dict) and "rule" in item else item
    rule_id = rule_dict.get("rule_id", "-") if isinstance(rule_dict, dict) else "-"
    name = rule_dict.get("name", "-") if isinstance(rule_dict, dict) else "-"

    # 1) Schema 校验
    try:
        rule = Rule.model_validate(rule_dict)
    except ValidationError as e:
        return "invalid", None, f"不符合 Rule Schema：{e}"
    except Exception as e:  # noqa: BLE001
        return "invalid", None, f"条目格式错误：{e}"

    # 2) 单规则自检（复用已有自检逻辑，坏规则拦截）
    chk = check_rule(rule)
    if not chk.ok:
        return "invalid", rule, _check_fail_reason(chk)

    # 3) ID 重复检查
    if db.get_rule(rule.rule_id) is not None:
        return "duplicate", rule, f"规则 ID 已存在：{name}（{rule_id}）"

    return "ok", rule, ""


@dataclass
class PreviewItem:
    """导入预览单条结果。

    字段: index 序号；rule_id 规则ID；name 规则名；
          status ok(可导入)/duplicate(已存在)/invalid(校验失败)；reason 说明。
    """
    index: int
    rule_id: str
    name: str
    status: str
    reason: str = ""


@dataclass
class ImportPreview:
    """导入预览结果（仅校验与分类，不写入任何数据）。

    字段: total 条目总数；items 逐条分类；file_error 文件级错误（解析失败等）。
    """
    total: int = 0
    items: list[PreviewItem] = field(default_factory=list)
    file_error: str | None = None

    @property
    def ok_count(self) -> int:
        """可导入条数。出参: int。"""
        return sum(1 for i in self.items if i.status == "ok")

    @property
    def duplicate_count(self) -> int:
        """已存在条数。出参: int。"""
        return sum(1 for i in self.items if i.status == "duplicate")

    @property
    def invalid_count(self) -> int:
        """校验失败条数。出参: int。"""
        return sum(1 for i in self.items if i.status == "invalid")


def preview_import(db, json_text: str) -> ImportPreview:
    """导入预览：逐条校验并分类，【不写入任何数据】。

    入参:
        db:        Database 实例（只读）。
        json_text: 导入的 JSON 文本。

    出参:
        ImportPreview: 预览结果（可导入/已存在/校验失败 + 原因）。
    """
    items, err = _parse_items(json_text)
    if err:
        return ImportPreview(total=0, items=[], file_error=err)

    preview = ImportPreview(total=len(items))
    for idx, item in enumerate(items):
        status, rule, reason = _classify_item(db, item)
        rule_id = rule.rule_id if rule is not None else (
            item.get("rule", item).get("rule_id", "-") if isinstance(item, dict) else "-"
        )
        name = rule.name if rule is not None else (
            item.get("rule", item).get("name", "-") if isinstance(item, dict) else "-"
        )
        preview.items.append(PreviewItem(index=idx, rule_id=rule_id, name=name, status=status, reason=reason))
    return preview


def import_rules_json(db, json_text: str, overwrite: bool = False) -> ImportResult:
    """批量导入规则（自动单规则自检，坏数据不落库）。

    导入流程（全部自研校验）：
    1. JSON 解析与 Schema 校验（Rule.model_validate）；
    2. 单规则自检（check_rule），不合格拦截（逐条标注序号+原因，不阻断其它条目）；
    3. 规则 ID 重复检查：默认跳过；overwrite=True 时覆盖写入；
    4. 写入规则并重建其版本快照（若导出含版本信息）。

    入参:
        db:        Database 实例。
        json_text: 导入的 JSON 文本（export_rules_json 的输出格式，
                   也兼容直接是规则数组的格式）。
        overwrite: 是否覆盖库内同 ID 规则（高危操作，调用方需先确认）。

    出参:
        ImportResult: 导入报告。
    """
    result = ImportResult()

    items, err = _parse_items(json_text)
    if err:
        result.failures.append({"index": "-", "rule_id": "-", "name": "（文件）", "reason": err})
        result.total = 0
        result.failed = 1
        return result

    result.total = len(items)
    for idx, item in enumerate(items):
        status, rule, reason = _classify_item(db, item)
        rule_id = rule.rule_id if rule is not None else "-"
        name = rule.name if rule is not None else "-"

        if status == "invalid":
            result.failures.append({"index": idx, "rule_id": rule_id, "name": name, "reason": reason})
            result.failed += 1
            continue

        if status == "duplicate":
            if not overwrite:
                result.failures.append(
                    {"index": idx, "rule_id": rule_id, "name": name, "reason": "规则 ID 已存在，跳过导入"}
                )
                result.failed += 1
                continue
            # 覆盖模式：直接覆写同 ID 规则并重建其版本快照
            db.upsert_rule(rule)
            if isinstance(item, dict) and isinstance(item.get("versions"), list):
                db.restore_versions(rule.rule_id, item["versions"])
            result.success += 1
            continue

        # 全新规则：写入 + 重建版本快照
        db.create_rule(rule)
        if isinstance(item, dict) and isinstance(item.get("versions"), list):
            db.restore_versions(rule.rule_id, item["versions"])
        result.success += 1

    return result
