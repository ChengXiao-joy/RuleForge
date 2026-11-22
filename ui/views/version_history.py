"""规则版本历史页（阶段3）。

功能：
- 选择规则后展示其全部版本快照列表（版本号 / 快照时间 / 备注 / 标签）；
- 手动给版本打标签（如 v1.0 / 上线版 / 草稿）；
- 任选两个版本做结构化差异对比（自研 diff，高亮展示）；
- 可选「AI 生成变更说明」：LLM 仅把结构化 diff 翻译成通俗中文，不参与比对。
"""
from __future__ import annotations

import json

import streamlit as st

from llm_layer.claude_client import LLMError
from llm_layer.explainer import explain_diff
from rule_engine.rule_diff import DiffEntry, DiffResult, diff_versions
from storage.db import get_db
from utils import config


def render() -> None:
    """渲染版本历史页。出参: None。"""
    st.header("⑤ 规则版本历史")
    st.caption("每次编辑自动留快照；可打标签、任选两个版本做结构化差异对比。")

    db = get_db()
    rows = db.list_rules()
    if not rows:
        st.caption("暂无规则。请先到「新增规则」页创建。")
        return

    rule_ids = [r["rule_id"] for r in rows]
    default_id = st.session_state.get("selected_rule_id", rule_ids[0])
    if default_id not in rule_ids:
        default_id = rule_ids[0]

    def _label(rid: str) -> str:
        r = next((x for x in rows if x["rule_id"] == rid), None)
        return f"{r['name']}（{rid[-8:]}）" if r else rid

    sel = st.selectbox("选择规则", rule_ids, index=rule_ids.index(default_id), format_func=_label)
    rule = db.get_rule(sel)
    if rule is None:
        st.error("规则不存在。")
        return

    versions = db.list_rule_versions(sel)
    if not versions:
        st.info("该规则暂无版本快照。（快照在新增/编辑保存时自动生成）")
        return

    st.caption(f"规则「{rule.name}」当前版本 v{rule.version}")

    # 高危操作：清空该规则全部版本快照（二次确认）
    if st.button("🗑️ 清空全部版本快照"):
        st.session_state["pending_clear_versions"] = sel
    if st.session_state.get("pending_clear_versions") == sel:
        st.warning(f"确认清空规则「{rule.name}」的全部版本快照吗？此操作不可恢复。")
        c1, c2, _ = st.columns([1, 1, 3])
        with c1:
            if st.button("✅ 确认清空"):
                n = db.clear_rule_versions(sel)
                st.session_state.pop("pending_clear_versions", None)
                st.success(f"已清空 {n} 条版本快照")
                st.rerun()
        with c2:
            if st.button("取消清空"):
                st.session_state.pop("pending_clear_versions", None)
                st.rerun()

    # ------------------------------------------------------------------
    # 版本快照列表
    # ------------------------------------------------------------------
    st.subheader("版本快照列表")
    table_rows = [
        {
            "版本": v["version"],
            "快照时间": v["created_at"],
            "备注": v["note"],
            "标签": v["tag"] or "—",
        }
        for v in versions
    ]
    st.dataframe(table_rows)

    # ------------------------------------------------------------------
    # 手动打标签
    # ------------------------------------------------------------------
    st.subheader("版本打标签")
    with st.form("tag_form"):
        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            tag_ver = st.selectbox(
                "版本", [v["version"] for v in versions],
                format_func=lambda x: f"v{x}",
            )
        with c2:
            tag_text = st.text_input("标签", placeholder="如 v1.0 / 上线版 / 草稿")
        with c3:
            tag_submit = st.form_submit_button("保存标签")
    if tag_submit:
        if not tag_text.strip():
            st.error("标签不能为空。")
        elif db.set_version_tag(sel, tag_ver, tag_text.strip()):
            st.success(f"已给 v{tag_ver} 打上标签「{tag_text.strip()}」")
            st.rerun()
        else:
            st.error(f"版本 v{tag_ver} 的快照不存在。")

    # ------------------------------------------------------------------
    # 版本对比
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("版本对比")
    ver_choices = [v["version"] for v in versions]
    c1, c2, _ = st.columns([1, 1, 2])
    with c1:
        from_ver = st.selectbox(
            "旧版本", ver_choices,
            index=min(1, len(ver_choices) - 1),
            format_func=lambda x: f"v{x}",
        )
    with c2:
        to_ver = st.selectbox(
            "新版本", ver_choices,
            index=0,
            format_func=lambda x: f"v{x}",
        )

    if st.button("🔍 对比两个版本"):
        va = next(v for v in versions if v["version"] == from_ver)
        vb = next(v for v in versions if v["version"] == to_ver)
        result = diff_versions(
            json.loads(va["after_json"]),
            json.loads(vb["after_json"]),
        )
        st.session_state["diff_view"] = {
            "from": from_ver,
            "to": to_ver,
            "entries": [e.to_dict() for e in result.entries],
        }

    if "diff_view" in st.session_state:
        d = st.session_state["diff_view"]
        st.markdown(f"**v{d['from']} → v{d['to']} 结构化差异**：")
        if not d["entries"]:
            st.info("两个版本完全一致，无任何差异。")
        else:
            for e in d["entries"]:
                if e["kind"] == "added":
                    st.success(f"➕ 新增  {e['path']}：{e['new']}")
                elif e["kind"] == "removed":
                    st.error(f"➖ 删除  {e['path']}：{e['old']}")
                else:
                    st.warning(f"✏️ 变更  {e['path']}：{e['old']} → {e['new']}")

            # LLM 仅翻译结构化 diff 结果，不参与比对
            if st.button("🤖 AI 生成变更说明"):
                if not config.has_api_key():
                    st.error("未配置 API Key，无法生成变更说明。")
                else:
                    result = DiffResult(entries=[DiffEntry(**e) for e in d["entries"]])
                    with st.spinner("正在生成变更说明…"):
                        try:
                            text = explain_diff(rule.name, d["from"], d["to"], result)
                        except LLMError as e:
                            st.error(f"变更说明生成失败：{e}")
                        else:
                            st.info(text)
