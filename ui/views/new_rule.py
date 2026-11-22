"""规则新增页：输入自然语言 -> 翻译 -> 预览 + 单条自检 -> 保存入库。"""
from __future__ import annotations

import streamlit as st

from llm_layer.parser import parse_natural_language
from rule_engine.schema import gen_rule_id, now_iso
from rule_engine.single_rule_checker import check_rule
from storage.db import get_db
from ui.views._shared import render_check_result
from utils import config


def render() -> None:
    """渲染规则新增页。出参: None。"""
    st.header("① 新增规则")
    st.caption("用一句话描述规则，系统翻译成结构化规则树并自动自检，确认后保存。")

    if not config.has_api_key():
        st.warning("⚠️ 未检测到 ANTHROPIC_API_KEY，解析功能不可用。请在项目根目录 .env 中配置后重启。")

    nl_text = st.text_area(
        "输入自然语言规则描述",
        height=100,
        placeholder="例如：如果员工本月迟到超过3次，就发邮件提醒人事部门",
    )

    if st.button("解析并自检", disabled=not config.has_api_key()):
        if not nl_text.strip():
            st.error("请输入规则描述。")
        else:
            # 清除上一次的解析结果，避免残留旧预览
            st.session_state.pop("new_rule", None)
            st.session_state.pop("new_rule_check", None)
            with st.spinner("正在调用 Claude 解析…"):
                result = parse_natural_language(nl_text)

            # 记录解析日志（本地 SQLite，便于排查问题）
            get_db().log_parse(
                nl_text.strip(),
                result.success,
                result.attempts,
                "rule"
                if (result.success and result.rule is not None)
                else ("clarification" if (result.success and result.needs_clarification) else None),
                result.error,
            )

            if result.success and result.rule is not None:
                rule = result.rule
                # 保留用户原始自然语言；重新生成唯一 ID 与时间戳
                rule.description = nl_text.strip()
                rule.rule_id = gen_rule_id()
                rule.created_at = rule.updated_at = now_iso()
                # 新增后自动触发单规则自检（联动逻辑）
                check = check_rule(rule)
                st.session_state["new_rule"] = rule
                st.session_state["new_rule_check"] = check
                st.success(f"✅ 解析成功（第 {result.attempts} 次尝试）")
            elif result.success and result.needs_clarification:
                st.info(f"❓ 需要澄清：{result.clarification_message}")
                for q in result.questions:
                    st.write(f"- {q}")
            else:
                st.error(f"❌ 解析失败：{result.error}")

    # 展示解析结果 + 自检 + 保存
    if "new_rule" in st.session_state:
        rule = st.session_state["new_rule"]
        check = st.session_state["new_rule_check"]

        st.divider()
        st.subheader("结构化规则树预览")
        st.json(rule.model_dump())

        st.subheader("单条自检告警")
        render_check_result(check)

        if st.button("💾 保存规则"):
            # 保存时再次自动触发单规则自检（联动逻辑，任务5要求）
            st.session_state["new_rule_check"] = check_rule(rule)
            try:
                get_db().create_rule(rule)
            except ValueError as e:
                st.error(f"保存失败：{e}")
            else:
                st.success(f"规则已保存：{rule.rule_id}")
                del st.session_state["new_rule"]
                del st.session_state["new_rule_check"]
