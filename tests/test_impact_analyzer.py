"""规则影响分析单元测试（离线）。

覆盖：冲突规则识别、覆盖下游/上游识别、依赖识别、坏规则跳过、无关联边界。
"""
from rule_engine import schema
from rule_engine.impact_analyzer import analyze_impact


def _leaf(field: str, op: str, value=None) -> schema.ConditionLeaf:
    """构造条件叶子。出参: ConditionLeaf。"""
    return schema.ConditionLeaf(field=field, operator=op, value=value)


def _act(action_type: str, target: str = "t", params: dict | None = None) -> schema.Action:
    """构造动作节点。出参: Action。"""
    return schema.Action(action_type=action_type, target=target, params=params or {})


def _rule(rid, name, conditions, actions, priority=50) -> schema.Rule:
    """构造规则。出参: Rule。"""
    return schema.Rule(
        rule_id=rid, name=name, conditions=conditions, actions=actions, priority=priority
    )


def test_impact_conflicts():
    target = _rule("T", "目标", _leaf("x", "gt", 10), [_act("approve")])
    other = _rule("O", "互斥", _leaf("x", "gt", 10), [_act("block")])
    r = analyze_impact(target, [target, other])
    assert len(r.conflicts) == 1
    assert r.conflicts[0].conflict_type == "mutual_exclusion"
    assert not r.covered_downstream and not r.dependents


def test_impact_covered_downstream():
    # target 更宽（同优先级）覆盖 other
    target = _rule("T", "宽", _leaf("x", "gt", 10), [_act("notify")], priority=50)
    other = _rule("O", "窄", _leaf("x", "gt", 20), [_act("notify")], priority=50)
    r = analyze_impact(target, [target, other])
    assert [x.rule_id for x in r.covered_downstream] == ["O"]
    assert not r.covering_upstream


def test_impact_covering_upstream():
    # 反向分析：other（更窄）被 target（更宽）覆盖 → 上游是 target
    target = _rule("T", "宽", _leaf("x", "gt", 10), [_act("notify")], priority=50)
    other = _rule("O", "窄", _leaf("x", "gt", 20), [_act("notify")], priority=50)
    r = analyze_impact(other, [target, other])
    assert [x.rule_id for x in r.covering_upstream] == ["T"]
    assert not r.covered_downstream


def test_impact_dependents():
    # target 写入 discount_status 字段，dep 的条件引用它 → 依赖
    target = _rule("T", "写字段", _leaf("x", "gt", 10), [_act("set_field", target="discount_status")])
    dep = _rule("D", "依赖", _leaf("discount_status", "eq", "已打折"), [_act("notify")])
    unrelated = _rule("U", "无关", _leaf("y", "gt", 5), [_act("notify")])
    r = analyze_impact(target, [target, dep, unrelated])
    assert [d.rule_id for d in r.dependents] == ["D"]


def test_impact_dependents_via_tag():
    target = _rule("T", "打标签", _leaf("x", "gt", 10), [_act("tag", target="customer_level")])
    dep = _rule("D", "依赖", _leaf("customer_level", "eq", "高价值"), [_act("notify")])
    r = analyze_impact(target, [target, dep])
    assert [d.rule_id for d in r.dependents] == ["D"]


def test_impact_empty():
    target = _rule("T", "目标", _leaf("x", "gt", 10), [_act("notify")])
    other = _rule("O", "无关", _leaf("y", "gt", 5), [_act("notify")])
    r = analyze_impact(target, [target, other])
    assert r.is_empty


def test_impact_skips_bad_rules():
    target = _rule("T", "目标", _leaf("x", "gt", 10), [_act("notify")])
    bad = _rule("B", "坏", _leaf("金额", "gt", 1), [_act("notify")])
    r = analyze_impact(target, [target, bad])
    assert [b.rule_id for b in r.skipped_bad] == ["B"]


def test_impact_summary_text():
    target = _rule("T", "目标", _leaf("x", "gt", 10), [_act("set_field", target="f")])
    dep = _rule("D", "依赖", _leaf("f", "eq", 1), [_act("notify")])
    r = analyze_impact(target, [target, dep])
    assert "依赖" in r.summary_text()


def test_impact_only_conflicts_involving_target():
    """其它规则之间的冲突不应列入目标规则的影响范围。"""
    # 注意：approve vs block 才是「结论相反」（notify 与 block 不算互斥，见阶段2 约定）
    target = _rule("T", "目标", _leaf("x", "gt", 10), [_act("approve")])
    o1 = _rule("O1", "其它1", _leaf("z", "gt", 1), [_act("approve")])
    o2 = _rule("O2", "其它2", _leaf("z", "gt", 1), [_act("block")])  # 与 O1 互斥
    r = analyze_impact(target, [target, o1, o2])
    assert r.conflicts == []  # O1-O2 的互斥不涉及目标，不列入

    # 与目标互斥的规则应被识别
    o3 = _rule("O3", "互斥", _leaf("x", "gt", 10), [_act("block")])
    r2 = analyze_impact(target, [target, o3])
    assert len(r2.conflicts) == 1
