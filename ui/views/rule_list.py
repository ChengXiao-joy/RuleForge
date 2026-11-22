"""规则列表页：卡片化展示规则、跳转详情、删除、批量冲突扫描。"""
from __future__ import annotations

import streamlit as st

from rule_engine import schema
from rule_engine.conflict_detector import scan_rules
from storage.db import get_db

# 状态徽标（emoji + 文案，避免仅用颜色区分）
_STATUS_BADGE = {
    "enabled": "✅ 启用",
    "disabled": "⛔ 禁用",
    "draft": "✏️ 草稿",
}


def _skip_reason(check) -> str:
    """坏规则过滤原因摘要（取前3个自检错误）。

    入参:
        check: SingleRuleCheckResult 自检结果。

    出参:
        str: 过滤原因摘要。
    """
    errs = check.errors
    if not errs:
        return "单规则自检未通过（存在告警）"
    return f"单规则自检未通过：{'；'.join(f'[{i.code}] {i.message}' for i in errs[:3])}"


def _condition_summary(node, depth: int = 0) -> str:
    """把条件树压缩成一行可读摘要。

    入参:
        node:  条件树节点（ConditionLeaf / LogicNode）。
        depth: 递归深度（用于 or 分支加括号）。

    出参:
        str: 条件摘要文本（如 "order.amount gt 1000 且 customer.level eq VIP"）。
    """
    if isinstance(node, schema.ConditionLeaf):
        return f"{node.field} {node.operator} {node.value}"
    joiner = " 且 " if node.logic == "and" else " 或 "
    parts = [_condition_summary(c, depth + 1) for c in node.children]
    text = joiner.join(parts)
    if node.logic == "or" and depth > 0:
        text = f"({text})"
    return text


def render() -> None:
    """渲染规则列表页。出参: None。"""
    st.header("② 规则列表")
    st.caption("按优先级降序卡片化展示；选择下方规则可查看/编辑或删除。")

    db = get_db()
    rules = db.get_all_rules()  # 完整规则（含条件），用于卡片展示
    if not rules:
        st.info("暂无已保存规则。请到「新增规则」页用自然语言创建第一条规则。")
        return

    # 批量冲突扫描（按钮文案不变，兼容自动化测试）
    st.subheader("批量冲突扫描")
    if st.button("🔍 批量冲突扫描"):
        with st.spinner("正在扫描全部规则…"):
            result = scan_rules(rules)
            skipped = [(b.rule.rule_id, _skip_reason(b.check)) for b in result.bad_rules]
            db.save_scan_result(result.conflicts, skipped)
        st.session_state["flash_scan"] = (
            f"扫描完成：共 {result.total_rules} 条规则，参与比对 {result.scanned_rules} 条，"
            f"过滤坏规则 {len(result.bad_rules)} 条，发现 {len(result.conflicts)} 个冲突"
        )
        st.session_state["pending_nav"] = "冲突报告"
        st.rerun()

    st.divider()
    st.subheader(f"全部规则（{len(rules)}）")

    # 卡片化列表：每条规则一张卡片，关键字段高亮
    for r in sorted(rules, key=lambda x: (-x.priority, x.rule_id)):
        with st.container(border=True):
            top1, top2 = st.columns([4, 1])
            with top1:
                st.markdown(f"**{r.name}**　`{r.rule_id}`")
                st.caption(r.description if r.description else "（无描述）")
            with top2:
                st.markdown(f"优先级 **{r.priority}**")
                st.caption(_STATUS_BADGE.get(r.status, r.status))
            st.caption(
                f"分类 {r.category} · v{r.version} · 条件：{_condition_summary(r.conditions)}"
            )

    st.divider()
    st.subheader("操作")

    rule_ids = [r.rule_id for r in rules]

    def _label(rid: str) -> str:
        r = next((x for x in rules if x.rule_id == rid), None)
        return f"{r.name}（{rid[-8:]}）" if r else rid

    sel = st.selectbox("选择规则", rule_ids, format_func=_label)

    col1, col2, _ = st.columns([1, 1, 3])
    with col1:
        if st.button("🔍 查看 / 编辑"):
            st.session_state["selected_rule_id"] = sel
            st.session_state["pending_nav"] = "规则详情/编辑"
            st.rerun()
    with col2:
        if st.button("🗑️ 删除"):
            st.session_state["pending_delete"] = sel

    # 删除二次确认
    if "pending_delete" in st.session_state:
        target = st.session_state["pending_delete"]
        st.warning(f"确认删除规则 {target} 吗？此操作不可恢复，其历史版本将归档保留。")
        c1, c2, _ = st.columns([1, 1, 3])
        with c1:
            if st.button("✅ 确认删除"):
                db.delete_rule(target)
                del st.session_state["pending_delete"]
                st.success(f"已删除规则 {target}（其历史版本已归档保留）")
                st.rerun()
        with c2:
            if st.button("取消"):
                del st.session_state["pending_delete"]
                st.rerun()
