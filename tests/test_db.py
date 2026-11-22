"""SQLite 存储层单元测试（离线，使用 pytest 临时目录）。"""
import json

import pytest

from rule_engine import schema
from storage.db import Database


def _rule() -> schema.Rule:
    """构造测试规则。出参: Rule。"""
    return schema.Rule(
        rule_id="R_DB_0001",
        name="测试规则",
        category="其他",
        conditions=schema.ConditionLeaf(field="x", operator="eq", value=1),
        actions=[schema.Action(action_type="notify", target="t")],
    )


def test_upsert_get_roundtrip(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert_rule(_rule())
    got = db.get_rule("R_DB_0001")
    assert got is not None
    assert got.name == "测试规则"
    assert got.conditions.operator == "eq"


def test_list_and_delete(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert_rule(_rule())
    assert len(db.list_rules()) == 1
    assert db.delete_rule("R_DB_0001") is True
    assert db.get_rule("R_DB_0001") is None
    assert db.delete_rule("R_DB_0001") is False


def test_upsert_updates(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert_rule(_rule())
    updated = _rule().model_copy(update={"name": "改名", "version": 2})
    db.upsert_rule(updated)
    got = db.get_rule("R_DB_0001")
    assert got.name == "改名"
    assert got.version == 2


def test_parse_log_and_stats(tmp_path):
    db = Database(tmp_path / "t.db")
    db.log_parse("x", True, 1, "rule", None)
    db.log_parse("y", False, 0, None, "error")
    db.log_parse("z", True, 2, "rule", None)
    s = db.parse_stats()
    assert s["total"] == 3
    assert s["success"] == 2
    assert s["first_attempt"] == 1


# ---- 阶段1：create_rule / update_rule ----
def test_create_rule_duplicate_raises(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule())
    with pytest.raises(ValueError):
        db.create_rule(_rule())


def test_update_rule_increments_version(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule())
    updated = db.update_rule(_rule().model_copy(update={"name": "改名"}))
    assert updated is not None
    assert updated.version == 2
    assert updated.name == "改名"
    assert db.get_rule("R_DB_0001").version == 2


def test_update_rule_missing_returns_none(tmp_path):
    db = Database(tmp_path / "t.db")
    assert db.update_rule(_rule()) is None


def test_get_all_rules(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule())
    db.create_rule(_rule().model_copy(update={"rule_id": "R_DB_0002", "priority": 80}))
    all_rules = db.get_all_rules()
    assert len(all_rules) == 2
    assert all_rules[0].rule_id == "R_DB_0002"  # 按优先级降序


# ---- 阶段2：冲突记录持久化 ----
def test_conflicts_persistence(tmp_path):
    db = Database(tmp_path / "t.db")
    c1 = schema.Conflict(
        conflict_id="C1", rule_a_id="A", rule_b_id="B",
        conflict_type="mutual_exclusion", severity="high", description="互斥",
    )
    c2 = schema.Conflict(
        conflict_id="C2", rule_a_id="A", rule_b_id="C",
        conflict_type="condition_overlap", severity="medium", description="覆盖",
    )
    db.save_scan_result([c1, c2], [("D", "单规则自检未通过")])

    conflicts = db.list_conflicts()
    assert len(conflicts) == 2
    assert conflicts[0]["conflict_id"] == "C1"  # high 排在 medium 前
    skipped = db.list_skipped()
    assert len(skipped) == 1
    assert skipped[0]["rule_id"] == "D"

    # 再次扫描快照式覆盖旧结果
    db.save_scan_result([], [])
    assert db.list_conflicts() == []
    assert db.list_skipped() == []


def test_delete_rule_cleans_related_conflicts(tmp_path):
    """删除规则时同步清除涉及它的冲突记录，保持报告与库一致。"""
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule())
    db.create_rule(_rule().model_copy(update={"rule_id": "R_DB_0002"}))
    db.save_scan_result(
        [
            schema.Conflict(conflict_id="C1", rule_a_id="R_DB_0001", rule_b_id="R_DB_0002",
                            conflict_type="mutual_exclusion", severity="high", description="x"),
            schema.Conflict(conflict_id="C2", rule_a_id="R_DB_0002", rule_b_id="R_DB_0003",
                            conflict_type="condition_overlap", severity="medium", description="y"),
        ],
        [("R_DB_0001", "自检未通过")],
    )
    assert len(db.list_conflicts()) == 2

    # 删除 R_DB_0001 → 涉及它的 C1 被清除，C2（不涉及）保留
    db.delete_rule("R_DB_0001")
    remaining = db.list_conflicts()
    assert [c["conflict_id"] for c in remaining] == ["C2"]
    assert db.list_skipped() == []


# ---- 阶段4：沙箱推演记录持久化 ----
def test_simulation_persistence_and_isolation(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule())

    # 保存推演记录（只写 simulation_runs 表）
    sim_id = db.save_simulation(
        {"x": 1}, '{"inputs": {"x": 1}, "final_actions": []}', 0
    )
    assert sim_id >= 1

    recs = db.list_simulations()
    assert len(recs) == 1
    assert recs[0]["hit_count"] == 0
    assert json.loads(recs[0]["inputs_json"]) == {"x": 1}

    # 隔离性：正式规则数据未被推演记录影响
    assert db.get_rule("R_DB_0001") is not None
    assert len(db.list_rules()) == 1


# ---- 阶段5：数据看板统计 ----
def test_dashboard_stats(tmp_path):
    db = Database(tmp_path / "t.db")
    db.create_rule(_rule())
    db.create_rule(
        schema.Rule(
            rule_id="BAD", name="坏",
            conditions=schema.ConditionLeaf(field="金额", operator="gt", value=1),
            actions=[schema.Action(action_type="notify", target="t")],
        )
    )
    db.save_scan_result(
        [schema.Conflict(
            conflict_id="C1", rule_a_id="A", rule_b_id="B",
            conflict_type="mutual_exclusion", severity="high", description="x",
        )],
        [],
    )
    db.save_simulation({"x": 1}, "{}", 0)

    stats = db.dashboard_stats()
    assert stats["rules"] == 2
    assert stats["bad_rules"] == 1  # 中文字段规则自检不合格
    assert stats["conflicts"] == 1
    assert stats["versions"] == 2  # 两条规则各 1 条初始快照
    assert stats["simulations"] == 1
