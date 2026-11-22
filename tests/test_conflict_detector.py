"""多规则冲突检测引擎单元测试（离线，不依赖 API）。

覆盖：冗余 / 互斥 / 覆盖 三类冲突、优先级方向、条件等价与子集判定、
坏规则过滤、无冲突边界。
"""
from rule_engine import schema
from rule_engine.conflict_detector import (
    detect_conflicts,
    scan_rules,
    to_dnf,
)


def _leaf(field: str, op: str, value=None) -> schema.ConditionLeaf:
    """构造条件叶子。出参: ConditionLeaf。"""
    return schema.ConditionLeaf(field=field, operator=op, value=value)


def _and(*children) -> schema.LogicNode:
    """构造 and 逻辑节点。出参: LogicNode。"""
    return schema.LogicNode(logic="and", children=list(children))


def _or(*children) -> schema.LogicNode:
    """构造 or 逻辑节点。出参: LogicNode。"""
    return schema.LogicNode(logic="or", children=list(children))


def _act(action_type: str, target: str = "t", params: dict | None = None) -> schema.Action:
    """构造动作节点。出参: Action。"""
    return schema.Action(action_type=action_type, target=target, params=params or {})


def _rule(
    rid: str,
    name: str,
    conditions,
    actions: list[schema.Action],
    priority: int = 50,
) -> schema.Rule:
    """构造规则。出参: Rule。"""
    return schema.Rule(
        rule_id=rid, name=name, conditions=conditions, actions=actions, priority=priority
    )


def _by_pair(conflicts, rid_a, rid_b):
    """按规则对查找冲突。出参: Conflict | None。"""
    for c in conflicts:
        if {c.rule_a_id, c.rule_b_id} == {rid_a, rid_b}:
            return c
    return None


# ---------------------------------------------------------------------------
# 冗余冲突（条件 + 动作完全一致）
# ---------------------------------------------------------------------------
def test_redundant_identical():
    a = _rule("A", "a", _leaf("x", "gt", 10), [_act("notify")])
    b = _rule("B", "b", _leaf("x", "gt", 10), [_act("notify")])
    conflicts = detect_conflicts([a, b])
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.conflict_type == "condition_overlap"
    assert c.severity == "low"  # 冗余 => low
    assert "冗余" in c.description


def test_redundant_and_order_insensitive():
    a = _rule("A", "a", _and(_leaf("x", "gt", 10), _leaf("y", "gt", 5)), [_act("notify")])
    b = _rule("B", "b", _and(_leaf("y", "gt", 5), _leaf("x", "gt", 10)), [_act("notify")])
    conflicts = detect_conflicts([a, b])
    assert len(conflicts) == 1
    assert conflicts[0].severity == "low"


def test_redundant_duplicate_or_branches():
    a = _rule("A", "a", _or(_leaf("x", "gt", 10), _leaf("x", "gt", 10)), [_act("notify")])
    b = _rule("B", "b", _leaf("x", "gt", 10), [_act("notify")])
    conflicts = detect_conflicts([a, b])
    assert len(conflicts) == 1
    assert conflicts[0].severity == "low"


# ---------------------------------------------------------------------------
# 互斥冲突（条件等价 + 结论相反）
# ---------------------------------------------------------------------------
def test_mutual_approve_vs_block():
    a = _rule("A", "a", _leaf("x", "gt", 10), [_act("approve")])
    b = _rule("B", "b", _leaf("x", "gt", 10), [_act("block")])
    conflicts = detect_conflicts([a, b])
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.conflict_type == "mutual_exclusion"
    assert c.severity == "high"


def test_mutual_set_field_opposite_values():
    a = _rule("A", "a", _leaf("x", "gt", 10), [_act("set_field", target="s", params={"status": "通过"})])
    b = _rule("B", "b", _leaf("x", "gt", 10), [_act("set_field", target="s", params={"status": "拒绝"})])
    conflicts = detect_conflicts([a, b])
    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == "mutual_exclusion"


# ---------------------------------------------------------------------------
# 覆盖冲突（条件真子集）
# ---------------------------------------------------------------------------
def test_cover_subset_same_priority_wider_covers():
    a = _rule("A", "a", _leaf("x", "gt", 10), [_act("notify")], priority=50)
    b = _rule("B", "b", _leaf("x", "gt", 20), [_act("notify")], priority=50)
    conflicts = detect_conflicts([a, b])
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.conflict_type == "condition_overlap"
    assert c.severity == "medium"  # 覆盖 => medium
    # 更宽的 A 覆盖更严的 B
    assert c.rule_a_id == "A" and c.rule_b_id == "B"


def test_cover_priority_reverses_direction():
    a = _rule("A", "a", _leaf("x", "gt", 10), [_act("notify")], priority=30)
    b = _rule("B", "b", _leaf("x", "gt", 20), [_act("notify")], priority=80)
    conflicts = detect_conflicts([a, b])
    assert len(conflicts) == 1
    c = conflicts[0]
    # B 更严但优先级更高 -> B 覆盖 A
    assert c.rule_a_id == "B" and c.rule_b_id == "A"


def test_cover_extra_condition_is_stricter():
    a = _rule("A", "a", _and(_leaf("x", "gt", 10), _leaf("y", "gt", 5)), [_act("notify")])
    b = _rule("B", "b", _leaf("x", "gt", 10), [_act("notify")])
    conflicts = detect_conflicts([a, b])
    assert len(conflicts) == 1
    # A 更严格（子集），B 更宽覆盖 A
    assert conflicts[0].rule_a_id == "B" and conflicts[0].rule_b_id == "A"


def test_cover_between_ranges():
    a = _rule("A", "a", _leaf("x", "between", [10, 50]), [_act("notify")])
    b = _rule("B", "b", _leaf("x", "between", [20, 30]), [_act("notify")])
    conflicts = detect_conflicts([a, b])
    assert len(conflicts) == 1
    assert conflicts[0].rule_a_id == "A"


def test_cover_eq_vs_in_list():
    a = _rule("A", "a", _leaf("x", "in", [1, 2, 3]), [_act("notify")])
    b = _rule("B", "b", _leaf("x", "eq", 1), [_act("notify")])
    conflicts = detect_conflicts([a, b])
    assert len(conflicts) == 1
    assert conflicts[0].rule_a_id == "A"


def test_cover_or_clause():
    # A: x>10 or x<5（两个子句），B: x>20（一个子句，被 A 的 x>10 子句蕴含）
    a = _rule("A", "a", _or(_leaf("x", "gt", 10), _leaf("x", "lt", 5)), [_act("notify")])
    b = _rule("B", "b", _leaf("x", "gt", 20), [_act("notify")])
    conflicts = detect_conflicts([a, b])
    assert len(conflicts) == 1
    assert conflicts[0].rule_a_id == "A" and conflicts[0].rule_b_id == "B"


# ---------------------------------------------------------------------------
# 无冲突边界
# ---------------------------------------------------------------------------
def test_no_conflict_different_fields():
    a = _rule("A", "a", _leaf("x", "gt", 10), [_act("notify")])
    b = _rule("B", "b", _leaf("y", "gt", 5), [_act("notify")])
    assert detect_conflicts([a, b]) == []


def test_no_conflict_overlapping_ranges():
    a = _rule("A", "a", _leaf("x", "gt", 10), [_act("notify")])
    b = _rule("B", "b", _leaf("x", "lt", 20), [_act("notify")])
    assert detect_conflicts([a, b]) == []


def test_no_conflict_equivalent_but_neither_same_nor_opposite():
    # 条件等价、动作不同但也不相反：不属于阶段2 三类冲突
    a = _rule("A", "a", _leaf("x", "gt", 10), [_act("notify")])
    b = _rule("B", "b", _leaf("x", "gt", 10), [_act("tag")])
    assert detect_conflicts([a, b]) == []


def test_no_conflict_equal_condition_same_action_field_diff():
    # 同条件但不同 target 的动作不算冗余（动作签名不同）
    a = _rule("A", "a", _leaf("x", "gt", 10), [_act("notify", target="hr")])
    b = _rule("B", "b", _leaf("x", "gt", 10), [_act("notify", target="boss")])
    assert detect_conflicts([a, b]) == []


def test_three_rules_two_conflicts():
    # notify（通知）与 block（拦截）不构成相反结论；approve vs block 才是相反
    a = _rule("A", "a", _leaf("x", "gt", 10), [_act("block")])
    b = _rule("B", "b", _leaf("x", "gt", 10), [_act("block")])    # 与 A 冗余
    c = _rule("C", "c", _leaf("x", "gt", 10), [_act("approve")])  # 与 A、B 互斥
    conflicts = detect_conflicts([a, b, c])
    assert len(conflicts) == 3  # A-B 冗余、A-C 互斥、B-C 互斥
    assert _by_pair(conflicts, "A", "B").severity == "low"
    assert _by_pair(conflicts, "A", "C").conflict_type == "mutual_exclusion"


# ---------------------------------------------------------------------------
# 坏规则过滤（扫描入口）
# ---------------------------------------------------------------------------
def test_scan_filters_bad_rules():
    good_a = _rule("A", "a", _leaf("x", "gt", 10), [_act("notify")])
    good_b = _rule("B", "b", _leaf("x", "gt", 10), [_act("notify")])
    bad = _rule("C", "c", _leaf("迟到次数", "gt", 3), [_act("notify")])  # 字段非法
    result = scan_rules([good_a, good_b, bad])
    assert result.total_rules == 3
    assert result.scanned_rules == 2
    assert len(result.bad_rules) == 1
    assert result.bad_rules[0].rule.rule_id == "C"
    assert len(result.conflicts) == 1  # 仅 A-B 冗余
    assert result.conflicts[0].rule_a_id in ("A", "B")
    assert "C" not in (result.conflicts[0].rule_a_id, result.conflicts[0].rule_b_id)


# ---------------------------------------------------------------------------
# 辅助：DNF / 标签映射
# ---------------------------------------------------------------------------
def test_to_dnf_basic():
    dnf = to_dnf(_and(_leaf("x", "gt", 10), _leaf("y", "eq", 1)))
    assert len(dnf) == 1
    assert len(dnf[0]) == 2


def test_conflict_label_mapping():
    from llm_layer.explainer import conflict_label

    assert conflict_label(schema.Conflict(
        conflict_id="C1", rule_a_id="A", rule_b_id="B",
        conflict_type="mutual_exclusion", severity="high",
    )) == "互斥冲突"
    assert conflict_label(schema.Conflict(
        conflict_id="C2", rule_a_id="A", rule_b_id="B",
        conflict_type="condition_overlap", severity="medium",
    )) == "覆盖冲突"
    assert conflict_label(schema.Conflict(
        conflict_id="C3", rule_a_id="A", rule_b_id="B",
        conflict_type="condition_overlap", severity="low",
    )) == "冗余冲突"
