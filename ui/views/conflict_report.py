"""冲突报告页（阶段2）。

展示内容：
- 被过滤的坏规则标注（自检不合格，未参与多规则比对）；
- 冲突列表：每条冲突的类型标签、涉及两条规则预览、冲突原因说明；
- 可选「生成通俗解释」：LLM 仅把机器判定结果翻译成中文，不参与判断。
"""
from __future__ import annotations

import streamlit as st

from llm_layer.claude_client import LLMError
from llm_layer.explainer import explain_conflict
from rule_engine.schema import Conflict
from storage.db import get_db
from utils import config


def _label(c: dict) -> str:
    """冲突类型中文标签（确定性映射，与 conflict_detector 约定一致）。

    入参:
        c: 冲突记录 dict（含 conflict_type 与 severity）。

    出参:
        str: 中文类型标签（互斥冲突 / 覆盖冲突 / 冗余冲突）。
    """
    if c["conflict_type"] == "mutual_exclusion":
        return "互斥冲突"
    if c["conflict_type"] == "condition_overlap":
        return "冗余冲突" if c["severity"] == "low" else "覆盖冲突"
    return c["conflict_type"]


_SEV_MARK = {"high": "🔴", "medium": "🟠", "low": "🟡"}


def render() -> None:
    """渲染冲突报告页。出参: None。"""
    st.header("④ 冲突报告")
    st.caption("冲突由自研引擎判定，本页只读展示；LLM 仅用于把判定结果翻译成中文。")

    # 扫描完成后跳转过来的提示
    if "flash_scan" in st.session_state:
        st.success(st.session_state.pop("flash_scan"))

    db = get_db()
    skipped = db.list_skipped()
    conflicts = db.list_conflicts()

    # 坏规则标注区（验收条件3）
    if skipped:
        st.subheader("⚠️ 被过滤的坏规则（自检不合格，未参与多规则比对）")
        for s in skipped:
            rule = db.get_rule(s["rule_id"])
            name = rule.name if rule else "（规则已删除）"
            st.warning(f"规则「{name}」（{s['rule_id']}）：{s['reason']}")

    st.divider()
    st.subheader(f"冲突列表（{len(conflicts)}）")
    if not conflicts:
        st.info("暂无冲突记录。请到「规则列表」页点击【批量冲突扫描】生成报告。")
        return

    for c in conflicts:
        rule_a = db.get_rule(c["rule_a_id"])
        rule_b = db.get_rule(c["rule_b_id"])
        name_a = rule_a.name if rule_a else c["rule_a_id"]
        name_b = rule_b.name if rule_b else c["rule_b_id"]
        label = _label(c)
        mark = _SEV_MARK.get(c["severity"], "")

        # 默认全部展开：冲突原因、规则预览与「生成通俗解释」按钮直接可见
        with st.expander(f"{mark} {label}：{name_a} ↔ {name_b}", expanded=True):
            st.markdown(f"**冲突类型**：{label}（严重度 {c['severity']}）")
            st.markdown(f"**冲突原因**：{c['description']}")
            st.caption(f"检测时间：{c['detected_at']}　冲突ID：{c['conflict_id']}")

            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**规则A**：{name_a}（{c['rule_a_id']}）")
                if rule_a:
                    st.json(rule_a.model_dump())
                else:
                    st.caption("规则已删除，无法预览")
            with col2:
                st.markdown(f"**规则B**：{name_b}（{c['rule_b_id']}）")
                if rule_b:
                    st.json(rule_b.model_dump())
                else:
                    st.caption("规则已删除，无法预览")

            # LLM 通俗解释：LLM 仅翻译机器判定结果，不参与冲突判断
            if st.button("🤖 生成通俗解释", key=f"explain_{c['conflict_id']}"):
                if not config.has_api_key():
                    st.error("未配置 API Key，无法生成解释。")
                else:
                    with st.spinner("正在生成解释…"):
                        try:
                            text = explain_conflict(
                                Conflict.model_validate(c), rule_a, rule_b
                            )
                        except LLMError as e:
                            st.error(f"解释生成失败：{e}")
                        else:
                            st.info(text)
