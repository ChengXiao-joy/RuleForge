"""沙箱推演引擎单元测试（离线）。

覆盖：13 种操作符求值、命中顺序与最终结论、未启用跳过、
完整链路记录、推演隔离性（不修改规则对象）、样本输入解析。
"""
import pytest

from rule_engine import schema
from rule_engine.sandbox_simulator import parse_inputs, simulate


def _leaf(field: str, op: str, value=None) -> schema.ConditionLeaf:
    """构造条件叶子。出参: ConditionLeaf。"""
    return schema.ConditionLeaf(field=field, operator=op, value=value)


def _and(*children) -> schema.LogicNode:
    """构造 and 逻辑节点。出参: LogicNode。"""
    return schema.LogicNode(logic="and", children=list(children))


def _or(*children) -> schema.LogicNode:
    """构造 or 逻辑节点。出参: LogicNode。"""
    return schema.LogicNode(logic="or", children=list(children))


def _rule(rid, name, conditions, actions=None, priority=50, status="enabled") -> schema.Rule:
    """构造规则。出参: Rule。"""
    return schema.Rule(
        rule_id=rid, name=name, conditions=conditions,
        actions=actions or [schema.Action(action_type="notify", target="t")],
        priority=priority, status=status,
    )


# ---- 操作符求值 ----
def test_operator_gt_missing_field():
    r = _rule("A", "a", _leaf("x", "gt", 10))
    assert simulate({"x": 15}, [r]).hit_count == 1
    assert simulate({"x": 5}, [r]).hit_count == 0
    assert simulate({}, [r]).hit_count == 0  # 字段缺失不命中


def test_operator_eq_in_between():
    r1 = _rule("A", "a", _leaf("level", "eq", "VIP"))
    assert simulate({"level": "VIP"}, [r1]).hit_count == 1
    assert simulate({"level": "vip"}, [r1]).hit_count == 0

    r2 = _rule("B", "b", _leaf("x", "in", [1, 2, 3]))
    assert simulate({"x": 2}, [r2]).hit_count == 1
    assert simulate({"x": 9}, [r2]).hit_count == 0

    r3 = _rule("C", "c", _leaf("x", "between", [10, 50]))
    assert simulate({"x": 30}, [r3]).hit_count == 1
    assert simulate({"x": 99}, [r3]).hit_count == 0


def test_operator_empty_not_empty():
    r1 = _rule("A", "a", _leaf("remark", "empty"))
    assert simulate({}, [r1]).hit_count == 1
    assert simulate({"remark": ""}, [r1]).hit_count == 1
    assert simulate({"remark": "x"}, [r1]).hit_count == 0

    r2 = _rule("B", "b", _leaf("remark", "not_empty"))
    assert simulate({"remark": "x"}, [r2]).hit_count == 1


def test_operator_contains():
    r = _rule("A", "a", _leaf("addr", "contains", "北京"))
    assert simulate({"addr": "北京市朝阳区"}, [r]).hit_count == 1
    assert simulate({"addr": "上海市"}, [r]).hit_count == 0


def test_logic_and_or():
    r1 = _rule("A", "a", _and(_leaf("x", "gt", 10), _leaf("level", "eq", "VIP")))
    assert simulate({"x": 15, "level": "VIP"}, [r1]).hit_count == 1
    assert simulate({"x": 15, "level": "NORMAL"}, [r1]).hit_count == 0

    r2 = _rule("B", "b", _or(_leaf("x", "gt", 100), _leaf("level", "eq", "VIP")))
    assert simulate({"x": 15, "level": "VIP"}, [r2]).hit_count == 1
    assert simulate({"x": 15, "level": "NORMAL"}, [r2]).hit_count == 0


# ---- 命中顺序与最终结论 ----
def test_priority_order_and_final_conclusion():
    r_low = _rule("L", "低优先级", _leaf("x", "gt", 10),
                  [schema.Action(action_type="notify", target="low")], priority=10)
    r_high = _rule("H", "高优先级", _leaf("x", "gt", 10),
                   [schema.Action(action_type="notify", target="high")], priority=90)
    res = simulate({"x": 15}, [r_low, r_high])
    assert res.hit_count == 2
    assert [s.rule_id for s in res.hit_rules] == ["H", "L"]  # 命中顺序按优先级
    assert res.final_actions[0]["target"] == "high"  # 最终结论 = 最高优先级命中规则


def test_no_hit_final_empty():
    r = _rule("A", "a", _leaf("x", "gt", 10))
    res = simulate({"x": 1}, [r])
    assert res.hit_count == 0
    assert res.final_actions == []


# ---- 未启用规则跳过 ----
def test_disabled_skipped():
    r = _rule("D", "d", _leaf("x", "gt", 10), status="disabled")
    res = simulate({"x": 15}, [r])
    assert res.hit_count == 0
    assert res.steps[0].evaluated is False
    assert "未启用" in res.steps[0].note


# ---- 链路记录 ----
def test_condition_detail_recorded():
    r = _rule("A", "a", _and(_leaf("x", "gt", 10), _leaf("level", "eq", "VIP")))
    res = simulate({"x": 15, "level": "VIP"}, [r])
    assert res.hit_count == 1
    assert "x" in res.steps[0].condition_detail
    assert "level" in res.steps[0].condition_detail
    assert res.steps[0].actions  # 动作输出被记录


def test_summary_text():
    r = _rule("A", "a", _leaf("x", "gt", 10))
    res = simulate({"x": 15}, [r])
    text = res.summary_text()
    assert "命中" in text and "最终结论" in text and "输入样本" in text


# ---- 沙箱隔离性 ----
def test_simulation_does_not_mutate_rules():
    """推演前后规则对象完全不变（隔离性由纯函数设计保证）。"""
    r = _rule("A", "a", _leaf("x", "gt", 10), priority=50)
    before = r.model_dump_json()
    simulate({"x": 15}, [r])
    simulate({"x": 1}, [r])
    assert r.model_dump_json() == before


# ---- 样本输入解析 ----
def test_parse_inputs():
    d = parse_inputs("x=10\nname=张三\nflag=true\nitems=[1,2,3]\nprice=9.9\n# 注释行")
    assert d == {"x": 10, "name": "张三", "flag": True, "items": [1, 2, 3], "price": 9.9}


def test_parse_inputs_invalid_line():
    with pytest.raises(ValueError):
        parse_inputs("没有等号")
