"""批量导入导出单元测试（离线）。

覆盖：导出（全部/选中、含版本快照）、导入（成功、Schema 非法、自检坏规则拦截、
ID 重复、坏 JSON）、混合导入报告、导出导入往返一致性。
"""
import json

from rule_engine import schema
from storage.db import Database
from utils.rule_import_export import (
    export_rules_json,
    import_rules_json,
    preview_import,
)


def _rule(rid: str = "R1", name: str = "规则", value=1) -> schema.Rule:
    """构造合法规则。出参: Rule。"""
    return schema.Rule(
        rule_id=rid,
        name=name,
        conditions=schema.ConditionLeaf(field="x", operator="gt", value=value),
        actions=[schema.Action(action_type="notify", target="t")],
    )


def _bad_rule() -> schema.Rule:
    """构造自检不合格规则（中文字段）。出参: Rule。"""
    return schema.Rule(
        rule_id="BAD",
        name="坏规则",
        conditions=schema.ConditionLeaf(field="金额", operator="gt", value=1),
        actions=[schema.Action(action_type="notify", target="t")],
    )


# ---- 导出 ----
def test_export_all_includes_versions(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule())
    db.update_rule(_rule(name="改名"), note="备注")
    db.set_version_tag("R1", 2, "上线版")

    data = json.loads(export_rules_json(db))
    assert data["app"] == "RuleForge"
    assert "exported_at" in data
    assert len(data["rules"]) == 1

    item = data["rules"][0]
    assert item["rule"]["name"] == "改名"
    versions = item["versions"]
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[1]["note"] == "备注"
    assert versions[1]["tag"] == "上线版"
    assert versions[1]["after_json"]["name"] == "改名"


def test_export_selected_only(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule("R1"))
    db.create_rule(_rule("R2"))
    data = json.loads(export_rules_json(db, ["R1"]))
    assert [i["rule"]["rule_id"] for i in data["rules"]] == ["R1"]


# ---- 导入 ----
def test_import_success_and_report(tmp_path):
    src = Database(tmp_path / "src.db")
    src.create_rule(_rule("R1", "规则一"))
    text = export_rules_json(src)

    db = Database(tmp_path / "t.db")
    result = import_rules_json(db, text)
    assert result.total == 1 and result.success == 1 and result.failed == 0
    assert db.get_rule("R1").name == "规则一"
    assert "导入完成" in result.summary_text()


def test_import_bad_rule_blocked(tmp_path):
    """自检不合格的坏规则被拦截，绝不写入。"""
    db = Database(tmp_path / "t.db")
    payload = {"app": "RuleForge", "rules": [{"rule": _bad_rule().model_dump(), "versions": []}]}
    result = import_rules_json(db, json.dumps(payload, ensure_ascii=False))
    assert result.total == 1 and result.success == 0 and result.failed == 1
    assert "自检" in result.failures[0]["reason"]
    assert db.get_rule("BAD") is None  # 坏数据未落库


def test_import_schema_invalid_rejected(tmp_path):
    db = Database(tmp_path / "t.db")
    payload = {"rules": [{"rule_id": "X"}]}  # 缺必填字段
    result = import_rules_json(db, json.dumps(payload, ensure_ascii=False))
    assert result.failed == 1
    assert "Schema" in result.failures[0]["reason"]


def test_import_duplicate_id_blocked(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule("R1", "已有"))
    payload = {"rules": [{"rule": _rule("R1", "重复").model_dump(), "versions": []}]}
    result = import_rules_json(db, json.dumps(payload, ensure_ascii=False))
    assert result.failed == 1
    assert "已存在" in result.failures[0]["reason"]


def test_import_invalid_json(tmp_path):
    db = Database(tmp_path / "t.db")
    result = import_rules_json(db, "not json")
    assert result.failed == 1
    assert "解析失败" in result.failures[0]["reason"]


def test_import_mixed_report(tmp_path):
    """混合导入：好规则成功、坏规则失败，报告齐全。"""
    db = Database(tmp_path / "t.db")
    payload = {
        "rules": [
            {"rule": _rule("G1", "好规则").model_dump(), "versions": []},
            {"rule": _bad_rule().model_dump(), "versions": []},
        ]
    }
    result = import_rules_json(db, json.dumps(payload, ensure_ascii=False))
    assert result.total == 2 and result.success == 1 and result.failed == 1
    assert db.get_rule("G1") is not None
    assert db.get_rule("BAD") is None


# ---- 导入预览（不写入数据） ----
def test_preview_import_classifies_without_writing(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule("R1", "已有"))
    payload = {
        "rules": [
            {"rule": _rule("NEW1", "新规则").model_dump(), "versions": []},
            {"rule": _rule("R1", "重复").model_dump(), "versions": []},
            {"rule": _bad_rule().model_dump(), "versions": []},
        ]
    }
    p = preview_import(db, json.dumps(payload, ensure_ascii=False))
    assert p.file_error is None
    assert p.total == 3
    assert p.ok_count == 1
    assert p.duplicate_count == 1
    assert p.invalid_count == 1
    assert "自检" in p.items[2].reason
    # 预览绝不写入数据
    assert db.get_rule("NEW1") is None
    assert db.get_rule("BAD") is None
    assert len(db.list_rules()) == 1


def test_preview_import_file_error(tmp_path):
    db = Database(tmp_path / "t.db")
    p = preview_import(db, "not json")
    assert p.file_error is not None
    assert "解析失败" in p.file_error


# ---- 覆盖导入（高危，需显式 overwrite=True） ----
def test_import_overwrite_replaces_existing(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule("R1", "旧版"))
    db.update_rule(_rule("R1", "旧版v2"), note="备注")
    assert db.get_rule("R1").version == 2

    # 导出一份「新版」规则（同名 ID 不同内容）
    src = Database(tmp_path / "src.db")
    src.create_rule(_rule("R1", "新版内容"))
    text = export_rules_json(src)

    # 默认不覆盖：失败
    r1 = import_rules_json(db, text, overwrite=False)
    assert r1.success == 0 and r1.failed == 1
    assert db.get_rule("R1").name == "旧版v2"

    # 覆盖模式：成功替换
    r2 = import_rules_json(db, text, overwrite=True)
    assert r2.success == 1 and r2.failed == 0
    assert db.get_rule("R1").name == "新版内容"


# ---- 往返一致性 ----
def test_roundtrip_versions_restored(tmp_path):
    """导出→导入到新库：规则数据与版本快照完整一致。"""
    src = Database(tmp_path / "src.db")
    src.create_rule(_rule("R1", "原版"))
    src.update_rule(_rule("R1", "改版"), note="改名")
    src.set_version_tag("R1", 2, "上线版")
    text = export_rules_json(src)

    dst = Database(tmp_path / "dst.db")
    result = import_rules_json(dst, text)
    assert result.success == 1

    assert dst.get_rule("R1").name == "改版"
    versions = dst.list_rule_versions("R1")
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["tag"] == "上线版"
    assert versions[0]["note"] == "改名"
    assert versions[1]["before_json"] is None  # 首版无修改前
    assert json.loads(versions[1]["after_json"])["name"] == "原版"
