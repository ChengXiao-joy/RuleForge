"""RuleForge 规则工坊 Streamlit 主入口。

阶段1 页面：规则新增页 / 规则列表页 / 规则详情·编辑页（含删除）。
通过侧边栏导航切换。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 将项目根目录加入 sys.path：
# streamlit run ui/app.py 时 sys.path[0] 是 ui/ 目录，直接 import ui.views 会失败
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from ui.views import (
    conflict_report,
    dashboard,
    import_export,
    new_rule,
    rule_detail,
    rule_list,
    sandbox,
    version_history,
)
from utils.logger import get_logger

log = get_logger("ui")

st.set_page_config(page_title="RuleForge 规则工坊", page_icon="⚙️", layout="wide")

# ---------------------------------------------------------------------------
# 全局样式：统一视觉（主题感知，深浅色模式均可正常阅读）
# 使用 Streamlit CSS 变量 var(--text-color) / var(--primary-color) 等自动适配深浅色
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    /* 顶部标题栏 */
    .rf-header {
        padding: 0.6rem 0 0.9rem 0;
        border-bottom: 2px solid var(--primary-color);
        margin-bottom: 1rem;
    }
    .rf-title {
        font-size: 1.55rem;
        font-weight: 700;
        color: var(--text-color);
        line-height: 1.3;
    }
    .rf-slogan {
        font-size: 0.9rem;
        color: var(--text-color);
        opacity: 0.68;
        margin-top: 0.15rem;
    }
    .rf-version {
        display: inline-block;
        font-size: 0.75rem;
        color: var(--primary-color);
        border: 1px solid var(--primary-color);
        border-radius: 8px;
        padding: 0.05rem 0.5rem;
        margin-top: 0.35rem;
    }
    /* 指标卡片：数值突出、卡片化 */
    [data-testid="stMetric"] {
        background: var(--secondary-background-color);
        border-radius: 10px;
        padding: 0.7rem 0.9rem;
        border: 1px solid var(--border-color, rgba(128,128,128,0.15));
    }
    [data-testid="stMetricValue"] {
        font-size: 1.7rem;
        font-weight: 700;
        color: var(--primary-color);
    }
    /* 展开面板更清爽 */
    [data-testid="stExpander"] {
        border-radius: 10px;
    }
    /* 表格文字自动换行、避免横向滚动 */
    [data-testid="stDataFrame"] {
        overflow-x: auto;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# 顶部固定标题栏：项目名 + slogan + 版本标识
st.markdown(
    """
    <div class="rf-header">
        <div class="rf-title">⚙️ RuleForge 规则工坊</div>
        <div class="rf-slogan">自然语言规则编排 · LLM 只做翻译，确定性逻辑全自研</div>
        <span class="rf-version">v1.0</span>
    </div>
    """,
    unsafe_allow_html=True,
)

st.sidebar.title("⚙️ RuleForge 规则工坊")
st.sidebar.caption("v1.0 · 导入导出 + 数据看板 + 项目打包")

# 帮助弹窗：面向办公用户解释核心概念
with st.sidebar.popover("❓ 使用帮助"):
    st.markdown("**规则**：一条「当什么情况发生时，做什么事」的设定。")
    st.markdown("比如：当员工本月迟到超过3次时，发邮件提醒人事。")
    st.markdown("**单规则自检**：保存规则时，系统自动检查这条规则本身有没有毛病"
                "（比如条件自相矛盾、字段名不合法），有毛病会标红提示。")
    st.markdown("**沙箱仿真**：用一个虚构的输入样本，试跑库里所有规则，看会命中哪些、"
                "最终谁生效。纯演练，不会改动任何真实规则。")
    st.markdown("**版本快照**：每次修改规则都会自动留一份修改前/后的存档，"
                "可以对比任意两个版本的变化，也可以打标签（如「上线版」）。")

# 页面间跳转约定：各页面写 st.session_state["pending_nav"]，
# 必须在此处（radio 组件实例化之前）消费并写入 nav，
# 否则会触发 StreamlitWidgetAlreadyInstantiatedError。
if "pending_nav" in st.session_state:
    st.session_state["nav"] = st.session_state.pop("pending_nav")

page = st.sidebar.radio(
    "导航",
    ["📊 看板首页", "新增规则", "规则列表", "规则详情/编辑", "冲突报告", "版本历史", "沙箱仿真", "批量导入导出"],
    key="nav",
)

# 全局异常兜底：页面渲染出错时给出友好提示，避免白屏
try:
    if page == "📊 看板首页":
        dashboard.render()
    elif page == "新增规则":
        new_rule.render()
    elif page == "规则列表":
        rule_list.render()
    elif page == "规则详情/编辑":
        rule_detail.render()
    elif page == "冲突报告":
        conflict_report.render()
    elif page == "版本历史":
        version_history.render()
    elif page == "沙箱仿真":
        sandbox.render()
    else:
        import_export.render()
except Exception as e:  # noqa: BLE001
    log.exception("页面渲染异常")
    st.error(
        f"😵 页面出现异常：{type(e).__name__}：{e}\n\n"
        "详细错误已记录到 logs/ruleforge.log，可查看后反馈。"
    )
