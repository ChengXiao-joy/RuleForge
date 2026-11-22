"""规则版本快照单元测试（离线）。

覆盖：新增自动初始快照、编辑自动快照（含修改前后完整 JSON）、
手动打标签、删除规则时版本软删除归档。
"""
import json

from rule_engine import schema
from storage.db import Database


def _rule(rid: str = "R_V1", name: str = "测试", value=1) -> schema.Rule:
    """构造测试规则。出参: Rule。"""
    return schema.Rule(
        rule_id=rid,
        name=name,
        conditions=schema.ConditionLeaf(field="x", operator="gt", value=value),
        actions=[schema.Action(action_type="notify", target="t")],
    )


def test_create_generates_initial_snapshot(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule())
    versions = db.list_rule_versions("R_V1")
    assert len(versions) == 1
    v = versions[0]
    assert v["version"] == 1
    assert v["before_json"] is None  # 初始版本无「修改前」
    assert "R_V1" in v["after_json"]
    assert v["note"] == "初始创建"
    assert v["tag"] == ""
    assert v["archived"] == 0


def test_update_generates_snapshot_with_before_after(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule())
    db.update_rule(_rule(name="改名", value=99), note="改了名称和条件值")

    versions = db.list_rule_versions("R_V1")
    assert len(versions) == 2
    latest = versions[0]  # 按版本号降序，v2 在前
    assert latest["version"] == 2
    assert latest["note"] == "改了名称和条件值"

    before = json.loads(latest["before_json"])
    after = json.loads(latest["after_json"])
    assert before["name"] == "测试"
    assert before["conditions"]["value"] == 1
    assert after["name"] == "改名"
    assert after["conditions"]["value"] == 99


def test_multiple_edits_accumulate_versions(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule())
    db.update_rule(_rule(name="v2"))
    db.update_rule(_rule(name="v3"))
    versions = db.list_rule_versions("R_V1")
    assert [v["version"] for v in versions] == [3, 2, 1]


def test_set_tag(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule())
    assert db.set_version_tag("R_V1", 1, "上线版") is True
    v = db.list_rule_versions("R_V1")[0]
    assert v["tag"] == "上线版"
    # 不存在的版本返回 False
    assert db.set_version_tag("R_V1", 99, "x") is False


def test_delete_archives_versions_soft(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule())
    db.update_rule(_rule(name="改名"))
    assert db.delete_rule("R_V1") is True

    # 规则本体已删除
    assert db.get_rule("R_V1") is None
    # 版本快照仍在（软删除归档，未物理删除）
    archived = db.list_rule_versions("R_V1", include_archived=True)
    assert len(archived) == 2
    assert all(v["archived"] == 1 for v in archived)
    # 默认查询不含已归档版本
    assert db.list_rule_versions("R_V1") == []


def test_snapshot_note_optional(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule(), note="手动备注")
    assert db.list_rule_versions("R_V1")[0]["note"] == "手动备注"
    db.update_rule(_rule(name="x"))  # 不带备注
    assert db.list_rule_versions("R_V1")[0]["note"] == ""


def test_clear_rule_versions(tmp_path):
    """高危操作：清空某规则全部版本快照（物理删除）。"""
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule())
    db.update_rule(_rule(name="v2"))
    assert len(db.list_rule_versions("R_V1")) == 2
    n = db.clear_rule_versions("R_V1")
    assert n == 2
    assert db.list_rule_versions("R_V1") == []
    # 规则本体不受影响
    assert db.get_rule("R_V1") is not None
