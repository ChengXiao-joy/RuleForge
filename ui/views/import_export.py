"""批量导入导出页（阶段5）。

- 导出：全部/选中规则 → 生成 JSON（含版本快照基础信息）→ 下载文件；
- 导入：上传文件或粘贴 JSON → 自动单规则自检 → 坏规则拦截 → 导入报告。
校验与统计全部自研；LLM 不参与。
"""
from __future__ import annotations

import streamlit as st

from storage.db import get_db
from utils.rule_import_export import (
    export_rules_json,
    import_rules_json,
    preview_import,
)


def render() -> None:
    """渲染批量导入导出页。出参: None。"""
    st.header("⑧ 批量导入导出")
    st.caption("导出含版本快照，导入先预览再写入；坏规则会被自检拦截并写入报告。")
    db = get_db()

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    st.subheader("📤 批量导出")
    rows = db.list_rules()
    if not rows:
        st.caption("库内暂无规则。")
    else:
        ids = [r["rule_id"] for r in rows]

        def _label(rid: str) -> str:
            r = next((x for x in rows if x["rule_id"] == rid), None)
            return f"{r['name']}（{rid[-8:]}）" if r else rid

        export_all = st.checkbox("导出全部规则", value=True)
        selected: list[str] = []
        if not export_all:
            selected = st.multiselect("选择要导出的规则", ids, format_func=_label)

        if st.button("生成导出 JSON"):
            if not export_all and not selected:
                st.error("请勾选「导出全部」或至少选择一条规则。")
            else:
                export_ids = None if export_all else selected
                st.session_state["export_json"] = export_rules_json(db, export_ids)

        if "export_json" in st.session_state:
            text = st.session_state["export_json"]
            from rule_engine.schema import now_iso

            fname = f"ruleforge_rules_{now_iso()[:10].replace('-', '')}.json"
            st.download_button(
                "⬇️ 下载 JSON 文件", text, file_name=fname, mime="application/json"
            )
            with st.expander("预览导出内容"):
                st.code(text)

    # ------------------------------------------------------------------
    # 导入
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("📥 批量导入")
    st.caption(
        "导入时自动执行单规则自检；坏规则会被拦截并写入报告，不会污染规则库。"
        "粘贴框可拖动右下角边框调整高度。"
    )

    # 允许用户拖动边框调整粘贴框高度（仅作用于本页的文本框）
    st.markdown(
        """
        <style>
        div[data-testid="stTextArea"] textarea {
            resize: vertical !important;
            min-height: 120px !important;
            max-height: 70vh;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    uploaded = st.file_uploader("上传 JSON 文件", type=["json"])
    pasted = st.text_area("或直接粘贴 JSON 内容", height=120)

    # 高危操作：覆盖导入需双重确认
    overwrite = st.checkbox(
        "☑️ 覆盖已存在的规则（高危：将替换库内同 ID 规则，其历史版本快照将被重建）",
        value=False,
    )
    confirm_overwrite = True
    if overwrite:
        confirm_overwrite = st.checkbox("我确认要覆盖库内同 ID 规则", value=False)

    col1, col2, _ = st.columns([1, 1, 3])
    with col1:
        preview_clicked = st.button("🔍 预览导入")
    with col2:
        import_clicked = st.button("✅ 确认导入", disabled=not confirm_overwrite)

    # 读取导入文本（上传文件优先，其次粘贴内容）
    text: str | None = None
    if uploaded is not None:
        text = uploaded.getvalue().decode("utf-8")
    elif pasted.strip():
        text = pasted.strip()

    if preview_clicked or import_clicked:
        if text is None:
            st.error("请上传文件或粘贴 JSON 内容。")
        else:
            if preview_clicked:
                # 预览：只校验分类，不写入任何数据
                st.session_state["import_preview"] = preview_import(db, text)
            if import_clicked:
                result = import_rules_json(db, text, overwrite=overwrite)
                st.session_state["import_report"] = result
                st.session_state.pop("import_preview", None)
                if result.failed == 0:
                    st.success(f"✅ 全部导入成功：{result.success} 条")
                else:
                    st.warning(
                        f"导入完成：成功 {result.success} 条，失败 {result.failed} 条（详见下方报告）"
                    )

    # 导入预览面板（确认前展示，不落库）
    if "import_preview" in st.session_state:
        p = st.session_state["import_preview"]
        with st.expander("📋 导入预览（尚未写入任何数据）", expanded=True):
            if p.file_error:
                st.error(p.file_error)
            else:
                st.markdown(
                    f"**共 {p.total} 条**：✅ 可导入 {p.ok_count} 条　"
                    f"⚠️ 已存在 {p.duplicate_count} 条（勾选「覆盖」并确认后可覆盖导入）　"
                    f"❌ 校验失败 {p.invalid_count} 条"
                )
                rows = [
                    {
                        "序号": i.index + 1,
                        "规则名": i.name,
                        "规则ID": i.rule_id,
                        "状态": {"ok": "✅ 可导入", "duplicate": "⚠️ 已存在", "invalid": "❌ 校验失败"}[i.status],
                        "说明": i.reason,
                    }
                    for i in p.items
                ]
                st.dataframe(rows)

    if "import_report" in st.session_state:
        report = st.session_state["import_report"]
        with st.expander("📋 导入报告", expanded=True):
            st.markdown(
                f"**共 {report.total} 条：成功 {report.success} 条，失败 {report.failed} 条**"
            )
            for f in report.failures:
                st.error(
                    f"✗ 第 {f.get('index', '-')} 条　{f['name']}（{f['rule_id']}）：{f['reason']}"
                )
