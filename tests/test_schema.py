"""Rule Schema 校验单元测试（离线，不依赖 API）。"""
import pytest
from pydantic import ValidationError

from rule_engine import schema


def _valid_rule() -> schema.Rule:
    """构造一条合法规则。出参: Rule。"""
    return schema.Rule(
        rule_id="R_TEST_0001",
        name="迟到提醒",
        category="人事",
        effective_scope=schema.Scope(target_type="employee"),
        conditions=schema.ConditionLeaf(field="attendance.late_count", operator="gt", value=3),
        actions=[schema.Action(action_type="notify", target="人事", channel="email")],
    )


def test_valid_rule_ok():
    rule = _valid_rule()
    assert rule.status == "enabled"
    assert rule.priority == 50


def test_extra_field_rejected():
    """extra=forbid：出现未定义字段应被拒绝。"""
    data = _valid_rule().model_dump()
    data["unknown_field"] = 1
    with pytest.raises(ValidationError):
        schema.Rule.model_validate(data)


def test_between_requires_two_values():
    with pytest.raises(ValidationError):
        schema.ConditionLeaf(field="x", operator="between", value=[1])
    # 合法形态
    schema.ConditionLeaf(field="x", operator="between", value=[1, 5])


def test_in_requires_list():
    with pytest.raises(ValidationError):
        schema.ConditionLeaf(field="x", operator="in", value="a")
    schema.ConditionLeaf(field="x", operator="in", value=["a", "b"])


def test_invalid_operator_rejected():
    with pytest.raises(ValidationError):
        schema.ConditionLeaf(field="x", operator="~=", value=1)


def test_nested_logic_tree():
    """验证逻辑节点 + 叶子节点的嵌套条件树。"""
    node = schema.LogicNode(
        logic="and",
        children=[
            schema.ConditionLeaf(field="a", operator="gt", value=1),
            schema.LogicNode(
                logic="or",
                children=[schema.ConditionLeaf(field="b", operator="eq", value="x")],
            ),
        ],
    )
    rule = _valid_rule().model_copy(update={"conditions": node})
    assert rule.conditions.type == "logic"
    assert len(rule.conditions.children) == 2


def test_llm_result_envelope():
    """LLM 返回信封的校验：rule 与 clarification 两种形态。"""
    ok = schema.LLMResult(result_type="rule", rule=_valid_rule())
    assert ok.rule.name == "迟到提醒"

    with pytest.raises(ValidationError):
        schema.LLMResult(result_type="rule", rule=None)
    with pytest.raises(ValidationError):
        schema.LLMResult(result_type="clarification", message=None)


def test_priority_range():
    # 注意：model_copy 默认不触发校验，需通过 model_validate 触发
    data = _valid_rule().model_dump()
    data["priority"] = 101
    with pytest.raises(ValidationError):
        schema.Rule.model_validate(data)
    data["priority"] = -1
    with pytest.raises(ValidationError):
        schema.Rule.model_validate(data)
