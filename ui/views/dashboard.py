"""数据看板首页（只读视图，不嵌入任何编辑表单）。

- 统计指标卡片：规则总数 / 启用 / 禁用 / 自检失败 / 版本快照总数 / 沙箱推演次数；
- 图表：规则状态分布、近7天版本变更记录（使用 Streamlit 默认主题，简单柱状图）；
- 首页导航汇总：全部功能模块直达入口。
统计全部由 storage 层自研 SQL/计算得出，与库内数据实时一致。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from storage.db import get_db


def render() -> None:
    """渲染看板首页（只读）。出参: None。"""
    st.header("📊 数据看板")
    st.caption("只读视图：本页仅展示统计信息，不提供任何编辑入口。")
    db = get_db()
    stats = db.dashboard_stats()

    cols = st.columns(6)
    cols[0].metric("规则总数", stats["rules"])
    cols[1].metric("启用规则", stats["enabled"])
    cols[2].metric("禁用规则", stats["disabled"])
    cols[3].metric("自检失败规则", stats["bad_rules"])
    cols[4].metric("版本快照总数", stats["versions"])
    cols[5].metric("沙箱推演次数", stats["simulations"])

    st.divider()
    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        st.subheader("规则状态分布")
        status_df = pd.DataFrame(
            {
                "状态": ["启用", "禁用", "草稿"],
                "数量": [stats["enabled"], stats["disabled"], stats["draft"]],
            }
        )
        st.bar_chart(status_df, x="状态", y="数量")
    with chart_col2:
        st.subheader("近7天版本变更记录")
        activity = db.version_activity_7d()
        activity_df = pd.DataFrame(activity)
        st.bar_chart(activity_df, x="日期", y="版本快照数")

    st.divider()
    st.subheader("功能导航")
    st.caption("点击按钮快速跳转到各功能页面：")

    r1 = st.columns(4)
    with r1[0]:
        if st.button("➕ 新增规则", use_container_width=True):
            st.session_state["pending_nav"] = "新增规则"
            st.rerun()
    with r1[1]:
        if st.button("📋 规则列表", use_container_width=True):
            st.session_state["pending_nav"] = "规则列表"
            st.rerun()
    with r1[2]:
        if st.button("🔍 规则详情/编辑", use_container_width=True):
            st.session_state["pending_nav"] = "规则详情/编辑"
            st.rerun()
    with r1[3]:
        if st.button("⚠️ 冲突报告", use_container_width=True):
            st.session_state["pending_nav"] = "冲突报告"
            st.rerun()

    r2 = st.columns(4)
    with r2[0]:
        if st.button("📜 版本历史", use_container_width=True):
            st.session_state["pending_nav"] = "版本历史"
            st.rerun()
    with r2[1]:
        if st.button("🧪 沙箱仿真", use_container_width=True):
            st.session_state["pending_nav"] = "沙箱仿真"
            st.rerun()
    with r2[2]:
        if st.button("📦 批量导入导出", use_container_width=True):
            st.session_state["pending_nav"] = "批量导入导出"
            st.rerun()
