"""页面共享组件。

提供单条自检结果的友好渲染，供新增页 / 详情页复用。
"""
from __future__ import annotations

import streamlit as st

from rule_engine.single_rule_checker import SingleRuleCheckResult


def render_check_result(check: SingleRuleCheckResult) -> None:
    """在页面友好渲染单条自检结果。

    入参:
        check: SingleRuleCheckResult 自检结果。

    出参: None。
    """
    if not check.issues:
        st.success("✅ 单条自检通过，未发现内部缺陷")
        return

    errors = check.errors
    warnings = check.warnings

    if errors:
        st.error(f"❌ 单条自检发现 {len(errors)} 个错误")
        for i in errors:
            loc = f"（位置：{i.path}）" if i.path else ""
            st.markdown(f"- **{i.code}**：{i.message}{loc}")
    if warnings:
        st.warning(f"⚠️ 单条自检发现 {len(warnings)} 个警告")
        for i in warnings:
            loc = f"（位置：{i.path}）" if i.path else ""
            st.markdown(f"- **{i.code}**：{i.message}{loc}")
