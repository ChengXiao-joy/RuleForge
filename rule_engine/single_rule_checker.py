"""单规则自检器（single_rule_checker）。

只做【单条规则内部】自检，绝不涉及多条规则之间的比对/冲突检测（那是后续阶段内容）。

检查项（对应阶段1 任务）：
1. 条件为空 —— 逻辑节点没有子条件；
2. 条件字段非法 —— 字段名不符合 snake_case / 点分路径命名规范；
3. 表达式语法错误 —— 比较值类型与操作符不匹配（如 gt 的比较值不是数值）；
4. 条件逻辑不可能成立（单条内部死条件）—— 同一 and 逻辑组内，同一字段的约束区间无交集、或等值冲突。

输出结构：
    CheckIssue(severity, code, message, path)
    SingleRuleCheckResult(rule_id, issues)  —— ok 表示无 error 级问题。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rule_engine.schema import ConditionLeaf, LogicNode, Rule

# 合法字段名：snake_case 或点分路径，如 attendance.late_count、order.amount
_FIELD_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

# 问题代码
C_EMPTY_CONDITIONS = "EMPTY_CONDITIONS"      # 条件为空
C_ILLEGAL_FIELD = "ILLEGAL_FIELD"            # 字段非法
C_BAD_VALUE_TYPE = "BAD_VALUE_TYPE"          # 表达式语法错误（value 类型不匹配）
C_BETWEEN_ORDER = "BETWEEN_ORDER_INVALID"    # between 区间顺序错误
C_EMPTY_IN_LIST = "EMPTY_IN_LIST"            # in/not_in 空列表
C_DEAD_CONDITION = "DEAD_CONDITION"          # 死条件
C_NO_ACTIONS = "NO_ACTIONS"                  # 动作为空（警告）

# 需要数值比较值的操作符
_NUMERIC_OPS = ("gt", "gte", "lt", "lte")


@dataclass
class CheckIssue:
    """单条自检发现的一个问题。

    字段:
        severity: 严重度，error / warning。
        code:     问题代码（见模块顶部常量）。
        message:  中文说明。
        path:     定位信息（如 conditions.children[0]）。
    """
    severity: str
    code: str
    message: str
    path: str = ""


@dataclass
class SingleRuleCheckResult:
    """单条自检结果。

    字段:
        rule_id: 规则 ID。
        issues:  问题列表。
    """
    rule_id: str
    issues: list[CheckIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[CheckIssue]:
        """返回 error 级问题列表。出参: list[CheckIssue]。"""
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[CheckIssue]:
        """返回 warning 级问题列表。出参: list[CheckIssue]。"""
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        """是否通过（无 error 级问题）。出参: bool。"""
        return not self.errors


# ---------------------------------------------------------------------------
# 数值区间（用于死条件检测）
# ---------------------------------------------------------------------------
@dataclass
class _Interval:
    low: float | None = None       # None 表示 -∞
    high: float | None = None      # None 表示 +∞
    low_open: bool = False         # 下界是否开区间
    high_open: bool = False        # 上界是否开区间


def _is_number(v) -> bool:
    """判断是否为数值（排除 bool）。入参 v: 任意值。出参: bool。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _type_name(v) -> str:
    """返回值的类型名（用于告警信息）。入参 v: 任意值。出参: str。"""
    return type(v).__name__


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def check_rule(rule: Rule) -> SingleRuleCheckResult:
    """对单条规则做内部自检。

    入参:
        rule: Rule 实例（已通过 Schema 校验）。

    出参:
        SingleRuleCheckResult: 自检结果，issues 为空或仅含 warning 时 ok=True。
    """
    result = SingleRuleCheckResult(rule_id=rule.rule_id)
    _check_node(rule.conditions, "conditions", result)
    if not rule.actions:
        result.issues.append(
            CheckIssue(
                "warning",
                C_NO_ACTIONS,
                "规则没有定义任何动作，满足条件后无操作可执行",
                "actions",
            )
        )
    return result


# ---------------------------------------------------------------------------
# 条件树递归检查
# ---------------------------------------------------------------------------
def _check_node(node, path: str, result: SingleRuleCheckResult, do_dead: bool = True) -> None:
    """递归检查条件树节点。

    入参:
        node:    条件树节点（LogicNode 或 ConditionLeaf）。
        path:    当前节点定位字符串。
        result:  累积检查结果。
        do_dead: 是否对本节点（若为 and）做死条件检测。用于避免嵌套 and 的重复检测。

    出参: None。
    """
    if isinstance(node, LogicNode):
        if not node.children:
            result.issues.append(
                CheckIssue(
                    "error",
                    C_EMPTY_CONDITIONS,
                    f"逻辑节点（{node.logic}）没有子条件，条件为空",
                    path,
                )
            )
            return

        # 仅对「and 链顶端」做死条件检测，覆盖整条 and 链
        if node.logic == "and" and do_dead:
            _check_and_dead(node, path, result)

        for i, child in enumerate(node.children):
            child_path = f"{path}.children[{i}]"
            # 嵌套 and：其死条件已被外层 and 覆盖，跳过；or 下的 and 仍需独立检测
            child_do_dead = not (
                node.logic == "and" and isinstance(child, LogicNode) and child.logic == "and"
            )
            _check_node(child, child_path, result, do_dead=child_do_dead)
    else:
        _check_leaf(node, path, result)


def _check_leaf(leaf: ConditionLeaf, path: str, result: SingleRuleCheckResult) -> None:
    """检查单个条件叶子：字段合法性与表达式语法。

    入参: leaf 条件叶子；path 定位；result 累积结果。出参: None。
    """
    # 检查项2：字段非法
    if not leaf.field or not _FIELD_PATTERN.match(leaf.field):
        result.issues.append(
            CheckIssue(
                "error",
                C_ILLEGAL_FIELD,
                f"条件字段名非法：'{leaf.field}'（应使用英文 snake_case 或点分路径，如 attendance.late_count）",
                path,
            )
        )

    # 检查项3：表达式语法错误（value 与 operator 类型匹配）
    _check_value_type(leaf, path, result)


def _check_value_type(leaf: ConditionLeaf, path: str, result: SingleRuleCheckResult) -> None:
    """检查比较值类型与操作符是否匹配。

    入参: leaf 条件叶子；path 定位；result 累积结果。出参: None。
    """
    op = leaf.operator
    v = leaf.value

    if op in _NUMERIC_OPS:
        if not _is_number(v):
            result.issues.append(
                CheckIssue(
                    "error",
                    C_BAD_VALUE_TYPE,
                    f"操作符 {op} 的比较值应为数值，实际为 {_type_name(v)}：{v!r}",
                    path,
                )
            )
    elif op == "between":
        if not (isinstance(v, list) and len(v) == 2 and all(_is_number(x) for x in v)):
            result.issues.append(
                CheckIssue(
                    "error",
                    C_BAD_VALUE_TYPE,
                    f"操作符 between 的比较值应为 [min, max] 两个数值，实际为 {v!r}",
                    path,
                )
            )
        elif v[0] > v[1]:
            result.issues.append(
                CheckIssue(
                    "error",
                    C_BETWEEN_ORDER,
                    f"between 区间顺序错误：最小值 {v[0]} 大于最大值 {v[1]}，条件永远不成立",
                    path,
                )
            )
    elif op in ("in", "not_in"):
        if not isinstance(v, list):
            result.issues.append(
                CheckIssue(
                    "error",
                    C_BAD_VALUE_TYPE,
                    f"操作符 {op} 的比较值应为列表，实际为 {_type_name(v)}",
                    path,
                )
            )
        elif not v:
            if op == "in":
                result.issues.append(
                    CheckIssue(
                        "error",
                        C_EMPTY_IN_LIST,
                        "in 的取值列表为空，条件永远不成立（死条件）",
                        path,
                    )
                )
            else:
                result.issues.append(
                    CheckIssue(
                        "warning",
                        C_EMPTY_IN_LIST,
                        "not_in 的取值列表为空，条件恒为真（可能无意义）",
                        path,
                    )
                )
    elif op in ("contains", "not_contains"):
        if not isinstance(v, str):
            result.issues.append(
                CheckIssue(
                    "warning",
                    C_BAD_VALUE_TYPE,
                    f"操作符 {op} 的比较值建议为字符串，实际为 {_type_name(v)}",
                    path,
                )
            )


# ---------------------------------------------------------------------------
# 死条件检测（仅单条规则内部）
# ---------------------------------------------------------------------------
def _collect_and_leaves(node, out: list[ConditionLeaf]) -> None:
    """收集 and 链上的所有叶子条件（递归展开嵌套 and，遇到 or 停止）。

    入参: node 条件树节点；out 输出叶子列表。出参: None。
    """
    if isinstance(node, LogicNode):
        if node.logic != "and":
            return
        for child in node.children:
            _collect_and_leaves(child, out)
    else:
        out.append(node)


def _check_and_dead(node, path: str, result: SingleRuleCheckResult) -> None:
    """检测一个 and 逻辑组内是否存在死条件（同一字段约束矛盾）。

    入参: node and 逻辑节点；path 定位；result 累积结果。出参: None。
    """
    leaves: list[ConditionLeaf] = []
    _collect_and_leaves(node, leaves)

    by_field: dict[str, list[ConditionLeaf]] = {}
    for leaf in leaves:
        if _FIELD_PATTERN.match(leaf.field):  # 仅对合法字段做死检测，非法字段已单独告警
            by_field.setdefault(leaf.field, []).append(leaf)

    for field, ls in by_field.items():
        _check_field_constraints(field, ls, path, result)


def _to_interval(leaf: ConditionLeaf) -> _Interval | None:
    """把单个条件叶子转换为数值区间（用于死条件检测）。

    入参: leaf 条件叶子。出参: _Interval | None（无法转为数值区间时返回 None）。
    """
    op = leaf.operator
    v = leaf.value
    if op == "eq":
        return _Interval(low=v, high=v) if _is_number(v) else None
    if op == "gt":
        return _Interval(low=v, low_open=True) if _is_number(v) else None
    if op == "gte":
        return _Interval(low=v) if _is_number(v) else None
    if op == "lt":
        return _Interval(high=v, high_open=True) if _is_number(v) else None
    if op == "lte":
        return _Interval(high=v) if _is_number(v) else None
    if op == "between" and isinstance(v, list) and len(v) == 2:
        return _Interval(low=v[0], high=v[1])
    return None


def _check_field_constraints(
    field: str, leaves: list[ConditionLeaf], path: str, result: SingleRuleCheckResult
) -> None:
    """检测同一字段在 and 组内的多个约束是否矛盾（死条件）。

    入参:
        field:  字段名。
        leaves: 该字段在 and 组内的所有叶子条件。
        path:   定位。
        result: 累积结果。

    出参: None。
    """
    # 1) 非数值等值冲突：同一字段被要求等于多个不同值
    eq_values = [l.value for l in leaves if l.operator == "eq" and not _is_number(l.value)]
    unique_eq = []
    for v in eq_values:
        if not any(v == u for u in unique_eq):
            unique_eq.append(v)
    if len(unique_eq) > 1:
        result.issues.append(
            CheckIssue(
                "error",
                C_DEAD_CONDITION,
                f"字段 '{field}' 被要求同时等于多个不同值 {unique_eq}，条件不可能成立",
                path,
            )
        )
        return

    # 2) 数值区间交集检测
    intervals = [iv for iv in (_to_interval(l) for l in leaves) if iv is not None]
    if not intervals:
        return

    lo_vals = [iv.low for iv in intervals if iv.low is not None]
    hi_vals = [iv.high for iv in intervals if iv.high is not None]
    if not lo_vals or not hi_vals:
        return  # 只有单侧约束，不会矛盾

    lo = max(lo_vals)
    hi = min(hi_vals)
    lo_open = any(iv.low == lo and iv.low_open for iv in intervals)
    hi_open = any(iv.high == hi and iv.high_open for iv in intervals)

    if lo > hi or (lo == hi and (lo_open or hi_open)):
        result.issues.append(
            CheckIssue(
                "error",
                C_DEAD_CONDITION,
                f"字段 '{field}' 的约束区间无交集（如要求同时满足矛盾的范围），条件不可能成立",
                path,
            )
        )
