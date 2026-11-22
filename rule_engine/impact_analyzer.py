"""规则影响分析（impact_analyzer）。

选中一条规则，自动扫描库内全部关联规则，识别三类关联：
1. 会被这条规则覆盖的下游规则（覆盖冲突中本规则是覆盖者）；
2. 和这条规则存在冲突的规则（互斥/冗余，复用阶段2 冲突检测引擎）；
3. 依赖这条规则输出结果的规则（自研：本规则的 set_field/tag 写入字段，
   被其它规则的条件树引用即视为依赖）。

全部为自研确定性代码，LLM 不参与识别；LLM 仅把结果翻译成通俗说明。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rule_engine.conflict_detector import detect_conflicts
from rule_engine.schema import ConditionLeaf, LogicNode, Rule, Conflict
from rule_engine.single_rule_checker import check_rule


@dataclass
class ImpactResult:
    """影响分析结果。

    字段:
        target_rule_id:     被分析的目标规则 ID。
        conflicts:          与目标存在冲突的规则（互斥/冗余冲突结构体）。
        covered_downstream: 被目标覆盖的下游规则。
        covering_upstream:  覆盖目标的上游规则。
        dependents:         依赖目标输出结果的规则。
        skipped_bad:        自检不合格被跳过的规则。
    """
    target_rule_id: str
    conflicts: list[Conflict] = field(default_factory=list)
    covered_downstream: list[Rule] = field(default_factory=list)
    covering_upstream: list[Rule] = field(default_factory=list)
    dependents: list[Rule] = field(default_factory=list)
    skipped_bad: list[Rule] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """是否无任何关联。出参: bool。"""
        return not (
            self.conflicts
            or self.covered_downstream
            or self.covering_upstream
            or self.dependents
        )

    def summary_text(self) -> str:
        """生成结构化摘要文本（供 LLM 翻译与页面展示）。出参: str。"""
        lines = []
        if self.conflicts:
            lines.append("冲突规则：")
            for c in self.conflicts:
                label = _conflict_label(c)
                lines.append(f"  - {c.rule_a_id} ↔ {c.rule_b_id}（{label}）：{c.description}")
        if self.covered_downstream:
            lines.append("被本规则覆盖的下游规则：")
            for r in self.covered_downstream:
                lines.append(f"  - {r.name}（{r.rule_id}）")
        if self.covering_upstream:
            lines.append("覆盖本规则的上游规则：")
            for r in self.covering_upstream:
                lines.append(f"  - {r.name}（{r.rule_id}）")
        if self.dependents:
            lines.append("依赖本规则输出结果的规则：")
            for r in self.dependents:
                lines.append(f"  - {r.name}（{r.rule_id}）")
        if self.skipped_bad:
            lines.append("自检不合格被跳过的规则：")
            for r in self.skipped_bad:
                lines.append(f"  - {r.name}（{r.rule_id}）")
        if not lines:
            return "未发现任何关联规则（无冲突、无覆盖、无依赖）。"
        return "\n".join(lines)


def _conflict_label(c: Conflict) -> str:
    """冲突类型中文标签（确定性映射，与阶段2 约定一致）。

    入参: c 冲突结构体。出参: str。
    """
    if c.conflict_type == "mutual_exclusion":
        return "互斥冲突"
    if c.conflict_type == "condition_overlap":
        return "冗余冲突" if c.severity == "low" else "覆盖冲突"
    return c.conflict_type


def _written_fields(rule: Rule) -> set[str]:
    """提取规则会写入/改写的字段（供依赖识别）。

    入参: rule 规则。出参: set[str] 字段名集合（set_field / tag 的 target）。
    """
    fields = set()
    for act in rule.actions:
        if act.action_type in ("set_field", "tag") and act.target:
            fields.add(act.target)
    return fields


def _collect_fields(node, out: set[str]) -> None:
    """收集条件树中引用的全部字段。

    入参: node 条件树节点；out 输出集合。出参: None。
    """
    if isinstance(node, ConditionLeaf):
        out.add(node.field)
    else:
        for child in node.children:
            _collect_fields(child, out)


def analyze_impact(target: Rule, all_rules: list[Rule]) -> ImpactResult:
    """分析单条规则的影响范围。

    入参:
        target:    被分析的目标规则。
        all_rules: 库内全部规则（只读，绝不修改）。

    出参:
        ImpactResult: 三类关联规则 + 被跳过的坏规则。
    """
    result = ImpactResult(target_rule_id=target.rule_id)

    others = [r for r in all_rules if r.rule_id != target.rule_id]
    good: list[Rule] = []
    for r in others:
        if check_rule(r).ok:
            good.append(r)
        else:
            result.skipped_bad.append(r)

    # 冲突 / 覆盖：复用阶段2 冲突检测引擎（目标规则与好规则两两比对）
    conflicts = detect_conflicts([target] + good)
    for c in conflicts:
        # 只保留涉及目标规则的冲突（其它规则之间的冲突不列入本规则的影响范围）
        if c.rule_a_id != target.rule_id and c.rule_b_id != target.rule_id:
            continue
        if c.conflict_type == "condition_overlap" and c.severity == "medium":
            # 覆盖冲突：rule_a = 覆盖者，rule_b = 被覆盖者
            if c.rule_a_id == target.rule_id:
                covered = next((r for r in good if r.rule_id == c.rule_b_id), None)
                if covered:
                    result.covered_downstream.append(covered)
            elif c.rule_b_id == target.rule_id:
                covering = next((r for r in good if r.rule_id == c.rule_a_id), None)
                if covering:
                    result.covering_upstream.append(covering)
        else:
            result.conflicts.append(c)

    # 依赖识别：目标的写入字段被其它规则条件引用
    written = _written_fields(target)
    if written:
        for r in good:
            fields: set[str] = set()
            _collect_fields(r.conditions, fields)
            if fields & written:
                result.dependents.append(r)

    return result
