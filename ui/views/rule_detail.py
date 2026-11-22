"""规则详情 / 编辑页：展示原始自然语言、规则树预览、单条自检；支持编辑保存。"""
from __future__ import annotations

import json

import streamlit as st

from llm_layer.claude_client import LLMError
from llm_layer.explainer import explain_impact
from rule_engine.impact_analyzer import analyze_impact
from rule_engine.schema import Rule, now_iso
from rule_engine.single_rule_checker import check_rule
from storage.db import get_db
from ui.views._shared import render_check_result
from utils import config


def _render_edit_form(rule: Rule) -> None:
    """渲染编辑表单（基础字段 + 完整 JSON 编辑）。

    入参:
        rule: 当前规则。

    出参: None。
    """
    # 表单组件绑定稳定 key：切换标签页后回到本页，正在编辑的内容不丢失
    k = rule.rule_id
    with st.form("edit_rule_form"):
        st.caption("基础字段")
        name = st.text_input("规则名称", rule.name, key=f"edit_name_{k}")
        priority = st.number_input("优先级（0~100）", 0, 100, rule.priority, step=5, key=f"edit_priority_{k}")
        status = st.selectbox(
            "状态", ["enabled", "disabled", "draft"],
            index=["enabled", "disabled", "draft"].index(rule.status),
            key=f"edit_status_{k}",
        )
        category = st.text_input("分类", rule.category, key=f"edit_category_{k}")
        description = st.text_area("自然语言描述", rule.description, height=70, key=f"edit_desc_{k}")
        note = st.text_input("修改备注", value="", placeholder="本次改了什么（会记录到版本快照）", key=f"edit_note_{k}")

        st.caption("条件树与动作（JSON，请保持 Schema 结构，勿新增字段）")
        payload_json = st.text_area(
            "结构化规则 JSON", rule.model_dump_json(indent=2), height=320, key=f"edit_json_{k}"
        )
        submitted = st.form_submit_button("保存修改")

    if submitted:
        try:
            data = json.loads(payload_json)
            # 以 rule_id 定位，忽略 JSON 中的 rule_id/version（版本由 update_rule 自动递增）
            merged = {
                **data,
                "rule_id": rule.rule_id,
                "name": name,
                "priority": priority,
                "status": status,
                "category": category,
                "description": description,
            }
            new_rule = Rule.model_validate(merged)  # 落库前严格校验 Schema
        except Exception as e:  # noqa: BLE001
            st.error(f"保存失败：JSON 不合法或不符合 Rule Schema —— {e}")
            return

        updated = get_db().update_rule(new_rule, note=note.strip())
        if updated is None:
            st.error(f"规则 {rule.rule_id} 已不存在，无法更新。")
            return

        # 修改后自动触发单规则自检（联动逻辑）
        check = check_rule(updated)
        st.session_state["edited_check"] = check
        st.session_state["edited_rule_id"] = updated.rule_id
        st.session_state["edited_check_time"] = now_iso()
        # 清空表单草稿缓存，下次编辑从最新规则数据开始
        for _fk in ("name", "priority", "status", "category", "desc", "note", "json"):
            st.session_state.pop(f"edit_{_fk}_{rule.rule_id}", None)
        # 把自检结论直接放进保存提示，让用户明确看到自检已执行
        n_err, n_warn = len(check.errors), len(check.warnings)
        if check.ok:
            check_summary = "自动自检 ✅ 通过，无内部缺陷"
        else:
            check_summary = f"自动自检 ❌ 发现 {n_err} 个错误" + (f"、{n_warn} 个警告" if n_warn else "")
        st.session_state["flash"] = (
            f"已保存 v{updated.version} —— {check_summary}（详见下方「单条自检告警」区）"
        )
        st.rerun()


def render() -> None:
    """渲染规则详情 / 编辑页。出参: None。"""
    st.header("③ 规则详情 / 编辑")
    st.caption("查看规则树、单条自检与影响分析；可编辑保存（自动留版本快照）。")

    # 展示上一次操作（如保存成功）的提示
    if "flash" in st.session_state:
        st.success(st.session_state.pop("flash"))

    db = get_db()
    rows = db.list_rules()
    if not rows:
        st.caption("暂无已保存规则。")
        return

    rule_ids = [r["rule_id"] for r in rows]
    default_id = st.session_state.get("selected_rule_id", rule_ids[0])
    if default_id not in rule_ids:
        default_id = rule_ids[0]

    def _label(rid: str) -> str:
        """选择框显示格式：规则名（ID尾号），便于区分同名规则。"""
        r = next((x for x in rows if x["rule_id"] == rid), None)
        return f"{r['name']}（{rid[-8:]}）" if r else rid

    sel = st.selectbox("选择规则", rule_ids, index=rule_ids.index(default_id), format_func=_label)
    rule = db.get_rule(sel)
    if rule is None:
        st.error("规则不存在。")
        return

    # 每次查看时自动触发单条自检
    check = check_rule(rule)
    # 若刚完成编辑，优先展示编辑后的自检结果
    if st.session_state.get("edited_rule_id") == sel and "edited_check" in st.session_state:
        check = st.session_state["edited_check"]

    st.divider()
    st.subheader("用户原始自然语言")
    st.write(rule.description if rule.description else "（无）")

    st.subheader("结构化规则树预览")
    st.json(rule.model_dump())

    st.subheader("单条自检告警")
    # 若展示的是本次保存后刚执行的自检结果，明确标注
    if st.session_state.get("edited_rule_id") == sel and "edited_check" in st.session_state:
        t = st.session_state.get("edited_check_time", "")
        st.caption(f"↳ 以下为保存后自动执行的自检结果（执行时间 {t}）")
    render_check_result(check)

    st.divider()
    col1, col2, col3, _ = st.columns([1, 1, 1, 2])
    with col1:
        if st.button("✏️ 编辑此规则"):
            st.session_state["editing_rule_id"] = sel
    with col2:
        if st.button("🗑️ 删除此规则"):
            # 二次确认：先挂起删除请求，确认后才真正删除
            st.session_state["pending_delete_detail"] = sel
    with col3:
        if st.button("📜 版本历史"):
            st.session_state["selected_rule_id"] = sel
            st.session_state["pending_nav"] = "版本历史"
            st.rerun()

    # 删除二次确认
    if st.session_state.get("pending_delete_detail") == sel:
        st.warning(
            f"确认删除规则「{rule.name}」（{sel}）吗？此操作不可恢复，其历史版本将归档保留。"
        )
        c1, c2, _ = st.columns([1, 1, 3])
        with c1:
            if st.button("✅ 确认删除"):
                db.delete_rule(sel)
                st.session_state.pop("pending_delete_detail", None)
                st.session_state.pop("selected_rule_id", None)
                st.session_state["flash"] = f"已删除规则 {sel}（其历史版本已归档保留）"
                st.rerun()
        with c2:
            if st.button("取消"):
                st.session_state.pop("pending_delete_detail", None)
                st.rerun()

    if st.session_state.get("editing_rule_id") == sel:
        st.subheader("编辑规则")
        _render_edit_form(rule)

    # ------------------------------------------------------------------
    # 规则影响分析面板（阶段4）
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("📊 规则影响分析")
    impact = analyze_impact(rule, db.get_all_rules())

    if impact.is_empty and not impact.skipped_bad:
        st.success("未发现关联规则（无冲突、无覆盖、无依赖）。")
    else:
        if impact.conflicts:
            st.warning(f"⚠️ 冲突规则 {len(impact.conflicts)} 条：")
            for c in impact.conflicts:
                other_id = c.rule_a_id if c.rule_b_id == sel else c.rule_b_id
                other = db.get_rule(other_id)
                other_name = other.name if other else other_id
                st.markdown(f"- {other_name}（{other_id}）：{c.description}")
        if impact.covered_downstream:
            st.info(
                "🔻 被本规则覆盖的下游规则 "
                + "、".join(r.name for r in impact.covered_downstream)
            )
        if impact.covering_upstream:
            st.info(
                "🔺 覆盖本规则的上游规则 "
                + "、".join(r.name for r in impact.covering_upstream)
            )
        if impact.dependents:
            st.info(
                "🔗 依赖本规则输出结果的规则 "
                + "、".join(r.name for r in impact.dependents)
            )
        if impact.skipped_bad:
            st.caption(
                "自检不合格被跳过："
                + "、".join(r.name for r in impact.skipped_bad)
            )

        # LLM 仅翻译影响分析结构化结果，不参与识别
        if st.button("🤖 AI 影响说明"):
            if not config.has_api_key():
                st.error("未配置 API Key，无法生成说明。")
            else:
                with st.spinner("正在生成影响说明…"):
                    try:
                        text = explain_impact(rule.name, impact)
                    except LLMError as e:
                        st.error(f"影响说明生成失败：{e}")
                    else:
                        st.info(text)
