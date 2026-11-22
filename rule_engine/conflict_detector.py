"""多规则冲突检测引擎（conflict_detector）。

只做【多条规则之间的两两冲突识别】，绝不包含：
- 沙箱仿真 / 自动修复 / 版本 Diff / 影响分析 等后续阶段能力。

冲突判断全部由自研确定性代码实现，LLM 不参与任何冲突判断（LLM 仅负责后续把
结构化冲突翻译成自然语言，见 llm_layer/explainer.py）。

识别三类冲突（映射到阶段0 冻结的 Conflict 结构体，不改 Schema）：
1. 冗余冲突：两条规则条件、动作完全一致            -> conflict_type=condition_overlap, severity=low
2. 覆盖冲突：一条规则条件范围是另一条的真子集       -> conflict_type=condition_overlap, severity=medium
3. 互斥冲突：触发条件等价但动作结论完全相反         -> conflict_type=mutual_exclusion, severity=high

核心算法（全部自研）：
- 条件树展平为析取范式 DNF：一组合取子句（frozenset[叶子]），规则匹配=任一子句成立；
- 叶子蕴含：把比较值统一转为「区间/集合」域，判断 M(a) ⊆ M(b)；
- 子句蕴含：对子句 B 的每个叶子，子句 A 中存在叶子蕴含它；
- 规则蕴含：规则 A 的每个子句都能被规则 B 的某子句蕴含；
- 动作签名：action_type + target + channel + message + params 归一化比较；
- 动作相反：approve vs block；set_field 同 target 同 key 不同值。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rule_engine.schema import (
    Action,
    ConditionLeaf,
    LogicNode,
    Rule,
    Conflict,
    gen_conflict_id,
    now_iso,
)
from rule_engine.single_rule_checker import SingleRuleCheckResult, check_rule

# 叶子键：(field, operator, frozen_value)
_LeafKey = tuple


# ---------------------------------------------------------------------------
# 值冻结与域表示
# ---------------------------------------------------------------------------
def _freeze(v):
    """把任意值冻结为可哈希结构（list/dict 递归转换、排序去序）。

    入参: v 任意值。出参: 可哈希的冻结值。
    """
    if isinstance(v, list):
        return tuple(sorted((_freeze(x) for x in v), key=repr))
    if isinstance(v, dict):
        return tuple(sorted((k, _freeze(vv)) for k, vv in v.items()))
    if isinstance(v, (set, frozenset)):
        return tuple(sorted((_freeze(x) for x in v), key=repr))
    return v


def _is_number(v) -> bool:
    """判断是否为数值（排除 bool）。入参 v: 任意值。出参: bool。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


@dataclass
class _Interval:
    """数值区间。low/high 为 None 表示 -∞/+∞；*_open 表示开区间。"""
    low: float | None = None
    high: float | None = None
    low_open: bool = False
    high_open: bool = False


def _to_domain(operator: str, value):
    """把单个条件叶子的操作符+值转换为可比较的「域」。

    入参:
        operator: 操作符。
        value:    比较值。

    出参:
        ("interval", _Interval) | ("set", frozenset) | None。
        None 表示该操作符不支持蕴含判定（ne/contains/empty 等，保守处理）。
    """
    if operator == "eq":
        if _is_number(value):
            return ("interval", _Interval(low=value, high=value))
        return ("set", frozenset({_freeze(value)}))
    if operator == "gt":
        return ("interval", _Interval(low=value, low_open=True)) if _is_number(value) else None
    if operator == "gte":
        return ("interval", _Interval(low=value)) if _is_number(value) else None
    if operator == "lt":
        return ("interval", _Interval(high=value, high_open=True)) if _is_number(value) else None
    if operator == "lte":
        return ("interval", _Interval(high=value)) if _is_number(value) else None
    if operator == "between" and isinstance(value, (list, tuple)) and len(value) == 2:
        return ("interval", _Interval(low=value[0], high=value[1]))
    if operator == "in" and isinstance(value, (list, tuple)):
        return ("set", frozenset(_freeze(x) for x in value))
    return None


def _point_in_interval(x, iv: _Interval) -> bool:
    """判断数值 x 是否落在区间 iv 内。

    入参: x 数值；iv 区间。出参: bool。
    """
    if not _is_number(x):
        return False
    if iv.low is not None:
        if x < iv.low or (x == iv.low and iv.low_open):
            return False
    if iv.high is not None:
        if x > iv.high or (x == iv.high and iv.high_open):
            return False
    return True


def _interval_contains(a: _Interval, b: _Interval) -> bool:
    """判断区间 a 是否完全包含区间 b（a 更宽）。

    入参: a、b 区间。出参: bool。
    """
    # 下界比较
    if a.low is None:
        low_ok = True
    elif b.low is None:
        low_ok = False
    elif a.low < b.low:
        low_ok = True
    elif a.low > b.low:
        low_ok = False
    else:  # 相等：a 闭包含 b 开；a 开不包含 b 闭
        low_ok = (not a.low_open) or b.low_open
    # 上界比较
    if a.high is None:
        high_ok = True
    elif b.high is None:
        high_ok = False
    elif a.high > b.high:
        high_ok = True
    elif a.high < b.high:
        high_ok = False
    else:
        high_ok = (not a.high_open) or b.high_open
    return low_ok and high_ok


def _domain_implies(a, b) -> bool:
    """判断域 a 是否蕴含域 b（M(a) ⊆ M(b)，a 更严格/范围更小）。

    入参: a、b 为 _to_domain 的返回值。出参: bool；无法判定时返回 False（保守）。
    """
    if a is None or b is None:
        return False
    if a[0] == "interval" and b[0] == "interval":
        return _interval_contains(b[1], a[1])  # b 包含 a
    if a[0] == "set" and b[0] == "set":
        return set(a[1]) <= set(b[1])          # a 集合 ⊆ b 集合
    if a[0] == "set" and b[0] == "interval":
        return all(_point_in_interval(x, b[1]) for x in a[1])
    if a[0] == "interval" and b[0] == "set":
        # 单点区间（如 eq 产生的 [v,v]）：点落在集合内则蕴含
        iv = a[1]
        if (
            iv.low is not None
            and iv.high is not None
            and iv.low == iv.high
            and not iv.low_open
            and not iv.high_open
        ):
            return _freeze(iv.low) in set(b[1])
        return False  # 真正区间 vs 集合，保守不判
    return False


# ---------------------------------------------------------------------------
# 条件树 -> DNF
# ---------------------------------------------------------------------------
def _leaf_key(leaf: ConditionLeaf) -> _LeafKey:
    """生成条件叶子的可哈希键。入参: leaf 条件叶子。出参: (field, operator, value)。"""
    return (leaf.field, leaf.operator, _freeze(leaf.value))


def to_dnf(node) -> list[frozenset]:
    """把条件树展平为析取范式 DNF。

    入参:
        node: 条件树根节点（LogicNode 或 ConditionLeaf）。

    出参:
        list[frozenset[_LeafKey]]: 一组合取子句（已去重），规则匹配=任一子句成立。
        frozenset() 表示恒真（无条件）。
    """
    if isinstance(node, ConditionLeaf):
        return [frozenset({_leaf_key(node)})]
    if not node.children:
        # 防御：空逻辑节点（正常已被单规则自检过滤，不参与比对）
        return [frozenset()] if node.logic == "and" else []
    if node.logic == "and":
        clauses: list[frozenset] = [frozenset()]
        for child in node.children:
            child_dnf = to_dnf(child)
            clauses = [a | b for a in clauses for b in child_dnf]
        return list(set(clauses))
    # or：各分支子句并集
    clauses = []
    for child in node.children:
        clauses.extend(to_dnf(child))
    return list(set(clauses))


# ---------------------------------------------------------------------------
# 蕴含判定
# ---------------------------------------------------------------------------
def _leaf_implies(a: _LeafKey, b: _LeafKey) -> bool:
    """判断叶子 a 是否蕴含叶子 b（M(a) ⊆ M(b)，a 更严格）。

    入参: a、b 叶子键。出参: bool；不同字段或无法判定返回 False。
    """
    if a[0] != b[0]:
        return False
    return _domain_implies(_to_domain(a[1], a[2]), _to_domain(b[1], b[2]))


def _clause_implies(c_a: frozenset, c_b: frozenset) -> bool:
    """判断合取子句 c_a 是否蕴含 c_b（c_a 更严格）。

    入参: c_a、c_b 子句（叶子键集合）。出参: bool。
    """
    for b in c_b:
        if not any(_leaf_implies(a, b) for a in c_a):
            return False
    return True


def _rule_implies(dnf_a: list[frozenset], dnf_b: list[frozenset]) -> bool:
    """判断规则条件 A 是否蕴含 B（M(A) ⊆ M(B)，A 条件更严格）。

    入参: dnf_a、dnf_b 两规则的 DNF。出参: bool。
    """
    return all(any(_clause_implies(c_a, c_b) for c_b in dnf_b) for c_a in dnf_a)


def _conditions_equivalent(dnf_a: list[frozenset], dnf_b: list[frozenset]) -> bool:
    """判断两规则条件是否语义等价（互为蕴含）。入参: 两 DNF。出参: bool。"""
    return _rule_implies(dnf_a, dnf_b) and _rule_implies(dnf_b, dnf_a)


# ---------------------------------------------------------------------------
# 动作比较
# ---------------------------------------------------------------------------
def _action_sig(act: Action) -> tuple:
    """生成动作签名（完全一致判定用）。

    入参: act 动作节点。出参: 可哈希签名元组。
    """
    return (act.action_type, act.target, act.channel, act.message, _freeze(act.params))


def _actions_equal(rule_a: Rule, rule_b: Rule) -> bool:
    """判断两条规则的动作是否完全一致（去序）。

    入参: rule_a、rule_b 规则。出参: bool。
    """
    sig_a = frozenset(_action_sig(x) for x in rule_a.actions)
    sig_b = frozenset(_action_sig(x) for x in rule_b.actions)
    return sig_a == sig_b


def _actions_opposite(rule_a: Rule, rule_b: Rule) -> bool:
    """判断两条规则的输出结论是否完全相反（取各自的主动作）。

    入参: rule_a、rule_b 规则。出参: bool。

    相反定义（自研、保守）：
    - approve vs block；
    - 均为 set_field 且 target 相同，存在同一参数 key 取值不同。
    """
    if not rule_a.actions or not rule_b.actions:
        return False
    a, b = rule_a.actions[0], rule_b.actions[0]
    if {a.action_type, b.action_type} == {"approve", "block"}:
        return True
    if a.action_type == b.action_type == "set_field" and a.target == b.target:
        common = set(a.params) & set(b.params)
        for k in common:
            if _freeze(a.params[k]) != _freeze(b.params[k]):
                return True
    return False


# ---------------------------------------------------------------------------
# 冲突检测主流程
# ---------------------------------------------------------------------------
def _make_conflict(
    rule_a: Rule,
    rule_b: Rule,
    conflict_type: str,
    severity: str,
    description: str,
) -> Conflict:
    """构造 Conflict 结构体（沿用阶段0 冻结 Schema）。

    入参: 两条规则、冲突类型、严重度、中文描述。出参: Conflict。
    """
    return Conflict(
        conflict_id=gen_conflict_id(),
        rule_a_id=rule_a.rule_id,
        rule_b_id=rule_b.rule_id,
        conflict_type=conflict_type,
        severity=severity,
        description=description,
        status="open",
        detected_at=now_iso(),
    )


def _compare_pair(rule_a: Rule, rule_b: Rule) -> Conflict | None:
    """两两比对：识别一对规则之间的冲突（三类之一）。

    入参:
        rule_a: 规则 A。
        rule_b: 规则 B。

    出参:
        Conflict | None: 识别到冲突返回结构体，无冲突返回 None。
    """
    dnf_a = to_dnf(rule_a.conditions)
    dnf_b = to_dnf(rule_b.conditions)

    a_implies_b = _rule_implies(dnf_a, dnf_b)  # A 条件更严格（A ⊆ B）
    b_implies_a = _rule_implies(dnf_b, dnf_a)  # B 条件更严格

    # 1) 条件等价：冗余 or 互斥
    if a_implies_b and b_implies_a:
        if _actions_equal(rule_a, rule_b):
            return _make_conflict(
                rule_a, rule_b, "condition_overlap", "low",
                f"冗余冲突：规则「{rule_a.name}」与「{rule_b.name}」的触发条件和动作完全一致，属于重复规则。",
            )
        if _actions_opposite(rule_a, rule_b):
            return _make_conflict(
                rule_a, rule_b, "mutual_exclusion", "high",
                f"互斥冲突：规则「{rule_a.name}」与「{rule_b.name}」触发条件相同，但输出结论完全相反。",
            )
        return None  # 条件等价但动作不同也不相反：不属于阶段2 三类冲突

    # 2) 条件真子集：覆盖冲突
    if a_implies_b or b_implies_a:
        if a_implies_b:  # A 更严格（A 是 B 的子集），B 更宽
            narrower, wider = rule_a, rule_b
        else:
            narrower, wider = rule_b, rule_a
        # 命中顺序：优先级高者先命中；优先级相同则更宽者先命中
        if wider.priority >= narrower.priority:
            coverer, covered = wider, narrower
        else:
            coverer, covered = narrower, wider
        return _make_conflict(
            coverer, covered, "condition_overlap", "medium",
            f"覆盖冲突：规则「{coverer.name}」的条件范围覆盖了「{covered.name}」"
            f"（前者优先级 {coverer.priority} 不低于后者 {covered.priority}，会优先命中），"
            f"被覆盖的规则将无法生效。",
        )
    return None


def detect_conflicts(rules: list[Rule]) -> list[Conflict]:
    """对多条规则做两两冲突检测（仅识别阶段2 三类冲突）。

    入参:
        rules: 已通过单规则自检的规则列表（坏规则应在上游过滤，见 scan_rules）。

    出参:
        list[Conflict]: 识别到的冲突列表（每条冲突含两条规则 ID、类型、严重度、描述）。
    """
    conflicts: list[Conflict] = []
    n = len(rules)
    for i in range(n):
        for j in range(i + 1, n):
            conflict = _compare_pair(rules[i], rules[j])
            if conflict is not None:
                conflicts.append(conflict)
    return conflicts


# ---------------------------------------------------------------------------
# 扫描入口（过滤坏规则 + 检测）
# ---------------------------------------------------------------------------
@dataclass
class BadRule:
    """自检不合格的坏规则（不参与多规则比对）。"""
    rule: Rule
    check: SingleRuleCheckResult


@dataclass
class ConflictScanResult:
    """一次冲突扫描的完整结果。

    字段:
        conflicts:      识别到的冲突列表。
        bad_rules:      自检不合格被过滤的坏规则（含自检结果，供报告标注）。
        total_rules:    扫描时读到的规则总数。
        scanned_rules:  参与多规则比对的规则数。
    """
    conflicts: list[Conflict] = field(default_factory=list)
    bad_rules: list[BadRule] = field(default_factory=list)
    total_rules: int = 0
    scanned_rules: int = 0


def scan_rules(rules: list[Rule]) -> ConflictScanResult:
    """冲突扫描入口：先过滤单规则自检不合格的坏规则，再对好规则两两比对。

    入参:
        rules: 库内全部规则。

    出参:
        ConflictScanResult: 冲突列表 + 被过滤的坏规则 + 统计。
    """
    bad: list[BadRule] = []
    good: list[Rule] = []
    for r in rules:
        chk = check_rule(r)
        if chk.ok:
            good.append(r)
        else:
            bad.append(BadRule(rule=r, check=chk))
    conflicts = detect_conflicts(good)
    return ConflictScanResult(
        conflicts=conflicts,
        bad_rules=bad,
        total_rules=len(rules),
        scanned_rules=len(good),
    )
