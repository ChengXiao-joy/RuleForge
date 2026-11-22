"""沙箱推演引擎（sandbox_simulator）。

实现独立的沙箱仿真推演：
- 纯函数设计：simulate(inputs, rules) 只读取传入的规则列表做内存推演，
  不接触数据库、不修改任何正式规则数据 —— 沙箱隔离由设计保证；
- 输入一组测试参数（字段->值），对库内 enabled 规则按优先级批量匹配，
  输出命中结果、命中顺序、最终输出结论；
- 记录完整推演链路：输入参数、每条规则的求值过程（中间流转）、命中与否、动作输出。

匹配/推演全部自研代码实现，LLM 不参与判定；LLM 仅把本模块输出的结构化链路
翻译成通俗解释（见 llm_layer/explainer.explain_simulation）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rule_engine.schema import Action, ConditionLeaf, LogicNode, Rule, now_iso


# ---------------------------------------------------------------------------
# 条件求值（自研，13 种操作符全覆盖）
# ---------------------------------------------------------------------------
def _is_number(v) -> bool:
    """判断是否为数值（排除 bool）。入参: v 任意值。出参: bool。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _eval_leaf(leaf: ConditionLeaf, val: Any) -> bool:
    """对单个条件叶子求值。

    入参:
        leaf: 条件叶子。
        val:  输入样本中该字段的值（字段缺失时为 None）。

    出参:
        bool: 条件是否成立（保守策略：值类型不符/字段缺失一律不成立）。
    """
    op = leaf.operator
    v = leaf.value
    if op == "eq":
        return val is not None and val == v
    if op == "ne":
        return val is not None and val != v
    if op == "gt":
        return _is_number(val) and val > v
    if op == "gte":
        return _is_number(val) and val >= v
    if op == "lt":
        return _is_number(val) and val < v
    if op == "lte":
        return _is_number(val) and val <= v
    if op == "contains":
        return isinstance(val, str) and isinstance(v, str) and v in val
    if op == "not_contains":
        return isinstance(val, str) and isinstance(v, str) and v not in val
    if op == "in":
        return isinstance(v, list) and val in v
    if op == "not_in":
        return isinstance(v, list) and val not in v
    if op == "between":
        return isinstance(v, list) and len(v) == 2 and _is_number(val) and v[0] <= val <= v[1]
    if op == "empty":
        return val is None or val == ""
    if op == "not_empty":
        return val is not None and val != ""
    return False


def _eval_node(node, inputs: dict[str, Any]) -> tuple[bool, str]:
    """递归求值条件树，返回 (是否成立, 求值过程文本)。

    入参:
        node:   条件树节点。
        inputs: 输入样本（字段 -> 值）。

    出参:
        (bool, str): 成立与否 + 中间流转文本。
    """
    if isinstance(node, ConditionLeaf):
        val = inputs.get(node.field)
        ok = _eval_leaf(node, val)
        return ok, f"{node.field}({val!r}) {node.operator} {node.value!r} → {'真' if ok else '假'}"
    if not node.children:
        return node.logic == "and", f"{node.logic.upper()}(空)"
    parts = []
    flags = []
    for child in node.children:
        ok, text = _eval_node(child, inputs)
        flags.append(ok)
        parts.append(text)
    if node.logic == "and":
        ok = all(flags)
    else:
        ok = any(flags)
    return ok, f"{node.logic.upper()} [{' 且 '.join(parts) if node.logic == 'and' else ' 或 '.join(parts)}] → {'真' if ok else '假'}"


# ---------------------------------------------------------------------------
# 推演结果结构
# ---------------------------------------------------------------------------
@dataclass
class StepResult:
    """一条规则的推演记录（中间流转结果）。

    字段:
        rule_id/rule_name/priority: 规则标识与优先级。
        status:                     规则状态（enabled/disabled/draft）。
        evaluated:                  是否参与求值（disabled/draft 跳过）。
        matched:                    条件是否命中。
        condition_detail:           条件求值过程文本。
        actions:                    命中时的动作输出列表。
        note:                       链路说明（如「未命中」「第1命中」「未启用跳过」）。
    """
    rule_id: str
    rule_name: str
    priority: int
    status: str
    evaluated: bool
    matched: bool
    condition_detail: str = ""
    actions: list[dict] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        """转为可序列化 dict。出参: dict。"""
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "priority": self.priority,
            "status": self.status,
            "evaluated": self.evaluated,
            "matched": self.matched,
            "condition_detail": self.condition_detail,
            "actions": self.actions,
            "note": self.note,
        }


@dataclass
class SimulationResult:
    """一次推演的完整结果。

    字段:
        inputs:         输入样本。
        steps:          全部规则的推演记录（按优先级降序）。
        hit_rules:      命中的规则记录（按命中顺序 = 优先级降序）。
        final_actions:  最终输出结论（最高优先级命中规则的动作；无命中为空）。
        simulated_at:   推演时间。
    """
    inputs: dict[str, Any]
    steps: list[StepResult] = field(default_factory=list)
    hit_rules: list[StepResult] = field(default_factory=list)
    final_actions: list[dict] = field(default_factory=list)
    simulated_at: str = ""

    def to_dict(self) -> dict:
        """转为可序列化 dict（用于落库与 LLM 翻译）。出参: dict。"""
        return {
            "inputs": self.inputs,
            "steps": [s.to_dict() for s in self.steps],
            "hit_rules": [s.to_dict() for s in self.hit_rules],
            "final_actions": self.final_actions,
            "simulated_at": self.simulated_at,
        }

    @property
    def hit_count(self) -> int:
        """命中规则数。出参: int。"""
        return len(self.hit_rules)

    def summary_text(self) -> str:
        """生成推演链路结构化摘要（供 LLM 翻译与页面展示）。

        出参:
            str: 完整链路文本。
        """
        lines = [f"输入样本：{self.inputs}", ""]
        for s in self.steps:
            if not s.evaluated:
                lines.append(f"- {s.rule_name}（{s.rule_id}）：{s.note}")
            elif s.matched:
                lines.append(
                    f"- {s.rule_name}（{s.rule_id}）优先级{s.priority}：✅ 命中（{s.note}）"
                    f"　求值：{s.condition_detail}　输出：{s.actions}"
                )
            else:
                lines.append(
                    f"- {s.rule_name}（{s.rule_id}）优先级{s.priority}：❌ 未命中　求值：{s.condition_detail}"
                )
        lines.append("")
        if self.hit_rules:
            top = self.hit_rules[0]
            lines.append(
                f"最终结论：按优先级由「{top.rule_name}」生效，输出：{self.final_actions}"
            )
        else:
            lines.append("最终结论：无任何规则命中。")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 推演主入口
# ---------------------------------------------------------------------------
def parse_inputs(text: str) -> dict:
    """解析样本输入文本为字段值字典（供页面与测试复用）。

    每行一个「字段=值」；值自动推断：整数/浮点数/布尔/列表/字符串。

    入参:
        text: 样本输入文本。

    出参:
        dict: 字段 -> 值。

    异常:
        ValueError: 存在不含「=」的非空行。
    """
    result: dict = {}
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"无效行（应为 字段=值）：{line}")
        k, v = line.split("=", 1)
        result[k.strip()] = _convert_input(v.strip())
    return result


def _convert_input(v: str):
    """把字符串值转换为推断类型。入参: v 字符串。出参: 转换后的值。"""
    if v == "":
        return ""
    low = v.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        items = [x.strip() for x in inner.split(",")] if inner else []
        return [_convert_input(x) for x in items]
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def _action_output(act: Action) -> dict:
    """把动作节点转为输出描述 dict。

    入参: act 动作节点。出参: dict。
    """
    return {
        "action_type": act.action_type,
        "target": act.target,
        "channel": act.channel,
        "message": act.message,
        "params": act.params,
    }


def simulate(inputs: dict[str, Any], rules: list[Rule]) -> SimulationResult:
    """执行一次沙箱推演（纯内存计算，不读写任何持久化数据）。

    入参:
        inputs: 测试输入样本（字段 -> 值），如 {"order.amount": 1500, "customer.level": "VIP"}。
        rules:  库内规则列表（只读使用，绝不修改）。

    出参:
        SimulationResult: 完整推演结果（链路 + 命中顺序 + 最终结论）。
    """
    result = SimulationResult(inputs=dict(inputs), simulated_at=now_iso())

    # 按优先级降序；优先级相同按 rule_id 保证确定性顺序
    ordered = sorted(rules, key=lambda r: (-r.priority, r.rule_id))

    for idx, rule in enumerate(ordered):
        step = StepResult(
            rule_id=rule.rule_id,
            rule_name=rule.name,
            priority=rule.priority,
            status=rule.status,
            evaluated=rule.status == "enabled",
            matched=False,
        )
        if rule.status != "enabled":
            step.note = f"状态 {rule.status}，未启用不参与推演"
            result.steps.append(step)
            continue

        ok, detail = _eval_node(rule.conditions, inputs)
        step.condition_detail = detail
        step.matched = ok
        if ok:
            step.actions = [_action_output(a) for a in rule.actions]
            step.note = f"第 {len(result.hit_rules) + 1} 命中"
            result.hit_rules.append(step)
        else:
            step.note = "未命中"
        result.steps.append(step)

    # 最终结论：最高优先级命中规则的动作（第一条命中）
    if result.hit_rules:
        result.final_actions = result.hit_rules[0].actions
    return result
