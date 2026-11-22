"""冲突自然语言解释器。

职责（严格边界）：把【冲突检测引擎已经识别出的结构化冲突数据】翻译成通俗易懂的
中文解释，供业务人员阅读。

红线：本模块只做翻译，不做任何冲突判断；冲突是否存在、类型是什么，完全由
rule_engine/conflict_detector.py 的确定性代码决定，LLM 不参与判断。
"""
from __future__ import annotations

from llm_layer import prompts
from llm_layer.claude_client import ClaudeClient, LLMError
from rule_engine.schema import Conflict, Rule
from utils.logger import get_logger

log = get_logger("llm.explainer")

# 冲突类型的中文标签（机器判定结果的可读化，非 LLM 生成）
CONFLICT_TYPE_LABELS = {
    "action_conflict": "动作冲突",
    "priority_conflict": "优先级冲突",
    "condition_overlap": "条件重叠",
    "mutual_exclusion": "互斥冲突",
}


def conflict_label(conflict: Conflict) -> str:
    """把冲突结构体映射为中文类型标签（确定性映射，与 LLM 无关）。

    映射规则（与 conflict_detector 约定一致，不改 Schema）：
    - mutual_exclusion                    -> 互斥冲突
    - condition_overlap + severity=medium -> 覆盖冲突
    - condition_overlap + severity=low    -> 冗余冲突

    入参:
        conflict: Conflict 结构体。

    出参:
        str: 中文类型标签。
    """
    if conflict.conflict_type == "mutual_exclusion":
        return "互斥冲突"
    if conflict.conflict_type == "condition_overlap":
        return "冗余冲突" if conflict.severity == "low" else "覆盖冲突"
    return CONFLICT_TYPE_LABELS.get(conflict.conflict_type, conflict.conflict_type)


def build_conflict_summary(
    conflict: Conflict,
    rule_a: Rule | None,
    rule_b: Rule | None,
) -> str:
    """由确定性代码生成冲突结构化摘要文本（供页面展示与 LLM 翻译使用）。

    入参:
        conflict: 冲突结构体。
        rule_a:   规则 A（可能已不存在，传 None）。
        rule_b:   规则 B（可能已不存在，传 None）。

    出参:
        str: 结构化摘要文本。
    """
    name_a = rule_a.name if rule_a else conflict.rule_a_id
    name_b = rule_b.name if rule_b else conflict.rule_b_id
    cond_a = _conditions_text(rule_a) if rule_a else "（规则已删除，无数据）"
    cond_b = _conditions_text(rule_b) if rule_b else "（规则已删除，无数据）"
    return (
        f"冲突类型：{conflict_label(conflict)}（严重度：{conflict.severity}）\n"
        f"规则A：{name_a}（ID {conflict.rule_a_id}），条件：{cond_a}\n"
        f"规则B：{name_b}（ID {conflict.rule_b_id}），条件：{cond_b}\n"
        f"机器判定说明：{conflict.description}"
    )


def _conditions_text(rule: Rule) -> str:
    """把规则条件树压缩成一行可读文本。

    入参: rule 规则。出参: str。
    """
    return str(rule.conditions.model_dump())[:200]


def explain_simulation(sim_result, client: ClaudeClient | None = None) -> str:
    """调用 LLM 把沙箱推演链路翻译成通俗中文说明。

    红线：推演/匹配全部由 rule_engine/sandbox_simulator.py 自研代码完成，
    本函数只把 SimulationResult 的结构化摘要交给 LLM 翻译，LLM 不参与判定。

    入参:
        sim_result: SimulationResult（sandbox_simulator.simulate 的输出）。
        client:     ClaudeClient；为空时内部创建（需已配置 API）。

    出参:
        str: 通俗中文推演说明。

    异常:
        LLMError: 调用失败时抛出（调用方负责捕获并降级展示）。
    """
    cli = client or ClaudeClient()
    log.info("开始解释一次沙箱推演（%d 条命中）", sim_result.hit_count)
    return cli.complete(
        prompts.EXPLAIN_SIMULATION_SYSTEM_PROMPT,
        prompts.build_explain_simulation_message(sim_result.summary_text()),
        max_tokens=1536,
    )


def explain_impact(
    rule_name: str,
    impact_result,
    client: ClaudeClient | None = None,
) -> str:
    """调用 LLM 把影响分析结果翻译成通俗中文说明。

    红线：影响识别全部由 rule_engine/impact_analyzer.py 自研代码完成，
    本函数只把 ImpactResult 的结构化摘要交给 LLM 翻译，LLM 不参与判定。

    入参:
        rule_name:     目标规则名称。
        impact_result: ImpactResult（impact_analyzer.analyze_impact 的输出）。
        client:        ClaudeClient；为空时内部创建（需已配置 API）。

    出参:
        str: 通俗中文影响说明。

    异常:
        LLMError: 调用失败时抛出（调用方负责捕获并降级展示）。
    """
    cli = client or ClaudeClient()
    log.info("开始解释规则 %s 的影响分析", rule_name)
    return cli.complete(
        prompts.EXPLAIN_IMPACT_SYSTEM_PROMPT,
        prompts.build_explain_impact_message(rule_name, impact_result.summary_text()),
        max_tokens=1536,
    )


def explain_diff(
    rule_name: str,
    from_version: int,
    to_version: int,
    diff_result,
    client: ClaudeClient | None = None,
) -> str:
    """调用 LLM 把机器比对出的版本差异翻译成通俗中文变更说明。

    红线：差异比对全部由 rule_engine/rule_diff.py 自研代码完成，
    本函数只把 DiffResult 的结构化摘要交给 LLM 翻译，LLM 不参与比对。

    入参:
        rule_name:    规则名称。
        from_version: 旧版本号。
        to_version:   新版本号。
        diff_result:  DiffResult（rule_diff.diff_versions 的输出）。
        client:       ClaudeClient；为空时内部创建（需已配置 API）。

    出参:
        str: 通俗中文变更说明。

    异常:
        LLMError: 调用失败时抛出（调用方负责捕获并降级展示）。
    """
    cli = client or ClaudeClient()
    log.info("开始解释规则 %s 的 v%s -> v%s 变更", rule_name, from_version, to_version)
    return cli.complete(
        prompts.EXPLAIN_DIFF_SYSTEM_PROMPT,
        prompts.build_explain_diff_message(
            rule_name, from_version, to_version, diff_result.summary_text()
        ),
        max_tokens=1024,
    )


def explain_conflict(
    conflict: Conflict,
    rule_a: Rule | None,
    rule_b: Rule | None,
    client: ClaudeClient | None = None,
) -> str:
    """调用 LLM 把冲突结构化数据翻译成通俗中文解释。

    入参:
        conflict: 冲突结构体（机器判定结果）。
        rule_a:   规则 A 对象（供 LLM 参考，可为 None）。
        rule_b:   规则 B 对象（供 LLM 参考，可为 None）。
        client:   ClaudeClient；为空时内部创建（需已配置 API）。

    出参:
        str: 通俗中文解释。

    异常:
        LLMError: 调用失败时抛出（调用方负责捕获并降级展示）。
    """
    cli = client or ClaudeClient()
    summary = build_conflict_summary(conflict, rule_a, rule_b)
    log.info("开始解释冲突 %s", conflict.conflict_id)
    return cli.complete(
        prompts.EXPLAIN_CONFLICT_SYSTEM_PROMPT,
        prompts.build_explain_conflict_message(summary),
        max_tokens=1024,
    )
