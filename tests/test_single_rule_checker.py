"""单规则自检器单元测试（离线，不依赖 API）。

覆盖检查项：条件为空、字段非法、表达式语法错误、单条内部死条件。
"""
from rule_engine import schema
from rule_engine.single_rule_checker import (
    C_BAD_VALUE_TYPE,
    C_BETWEEN_ORDER,
    C_DEAD_CONDITION,
    C_EMPTY_CONDITIONS,
    C_EMPTY_IN_LIST,
    C_ILLEGAL_FIELD,
    C_NO_ACTIONS,
    check_rule,
)


def _leaf(field: str, operator: str, value=None) -> schema.ConditionLeaf:
    """构造条件叶子。出参: ConditionLeaf。"""
    return schema.ConditionLeaf(field=field, operator=operator, value=value)


def _and(*children) -> schema.LogicNode:
    """构造 and 逻辑节点。出参: LogicNode。"""
    return schema.LogicNode(logic="and", children=list(children))


def _or(*children) -> schema.LogicNode:
    """构造 or 逻辑节点。出参: LogicNode。"""
    return schema.LogicNode(logic="or", children=list(children))


def _rule(conditions, actions=None) -> schema.Rule:
    """构造规则（默认带一个合法动作）。出参: Rule。"""
    return schema.Rule(
        rule_id="R1",
        name="测试规则",
        conditions=conditions,
        actions=actions if actions is not None else [schema.Action(action_type="notify", target="t")],
    )


# ---- 检查项1：条件为空 ----
def test_empty_logic_node():
    res = check_rule(_rule(_and()))
    assert not res.ok
    assert any(i.code == C_EMPTY_CONDITIONS for i in res.errors)


# ---- 检查项2：字段非法 ----
def test_illegal_field_chinese():
    res = check_rule(_rule(_leaf("迟到次数", "gt", 3)))
    assert any(i.code == C_ILLEGAL_FIELD for i in res.errors)


def test_illegal_field_empty():
    res = check_rule(_rule(_leaf("", "gt", 3)))
    assert any(i.code == C_ILLEGAL_FIELD for i in res.errors)


def test_legal_field_passes():
    res = check_rule(_rule(_leaf("attendance.monthly_late_count", "gt", 3)))
    assert not any(i.code == C_ILLEGAL_FIELD for i in res.issues)


# ---- 检查项3：表达式语法错误 ----
def test_bad_value_type_numeric():
    res = check_rule(_rule(_leaf("x", "gt", "3")))
    assert any(i.code == C_BAD_VALUE_TYPE for i in res.errors)


def test_bad_value_type_between_non_numeric():
    res = check_rule(_rule(_leaf("x", "between", [1, "a"])))
    assert any(i.code == C_BAD_VALUE_TYPE for i in res.errors)


def test_between_order_invalid():
    res = check_rule(_rule(_leaf("x", "between", [10, 5])))
    assert any(i.code == C_BETWEEN_ORDER for i in res.errors)


def test_empty_in_list_is_dead():
    res = check_rule(_rule(_leaf("x", "in", [])))
    assert any(i.code == C_EMPTY_IN_LIST and i.severity == "error" for i in res.errors)


def test_empty_not_in_list_warning():
    res = check_rule(_rule(_leaf("x", "not_in", [])))
    assert any(i.code == C_EMPTY_IN_LIST and i.severity == "warning" for i in res.warnings)


# ---- 检查项4：单条内部死条件 ----
def test_dead_condition_range_no_intersection():
    res = check_rule(_rule(_and(_leaf("x", "gt", 10), _leaf("x", "lt", 5))))
    assert any(i.code == C_DEAD_CONDITION for i in res.errors)


def test_dead_condition_eq_vs_range():
    res = check_rule(_rule(_and(_leaf("x", "eq", 5), _leaf("x", "gt", 10))))
    assert any(i.code == C_DEAD_CONDITION for i in res.errors)


def test_dead_condition_eq_conflict_string():
    res = check_rule(_rule(_and(_leaf("x", "eq", "A"), _leaf("x", "eq", "B"))))
    assert any(i.code == C_DEAD_CONDITION for i in res.errors)


def test_no_dead_condition_compatible():
    res = check_rule(_rule(_and(_leaf("x", "gt", 10), _leaf("x", "lt", 20))))
    assert res.ok


def test_touching_closed_bounds_not_dead():
    res = check_rule(_rule(_and(_leaf("x", "gte", 10), _leaf("x", "lte", 10))))
    assert res.ok


def test_touching_open_bound_is_dead():
    res = check_rule(_rule(_and(_leaf("x", "gt", 10), _leaf("x", "lte", 10))))
    assert any(i.code == C_DEAD_CONDITION for i in res.errors)


def test_dead_condition_inside_or_branch():
    res = check_rule(_rule(_or(_and(_leaf("x", "gt", 10), _leaf("x", "lt", 5)), _leaf("y", "eq", 1))))
    assert any(i.code == C_DEAD_CONDITION for i in res.errors)


def test_different_fields_not_dead():
    # 不同字段之间的约束不算矛盾
    res = check_rule(_rule(_and(_leaf("x", "gt", 10), _leaf("y", "lt", 5))))
    assert res.ok


# ---- 其它 ----
def test_valid_rule_ok():
    res = check_rule(_rule(_and(_leaf("a", "gt", 1), _leaf("b", "eq", "x"))))
    assert res.ok and not res.issues


def test_no_actions_warning():
    res = check_rule(_rule(_leaf("x", "gt", 1), actions=[]))
    assert any(i.code == C_NO_ACTIONS and i.severity == "warning" for i in res.warnings)
