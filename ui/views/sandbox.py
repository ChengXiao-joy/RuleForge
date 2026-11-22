"""沙箱仿真推演页（阶段4）。

功能：
- 输入测试样本数据（每行 字段=值）；
- 一键启动沙箱推演（纯内存计算，绝不修改正式规则数据）；
- 推演链路文本可视化（命中顺序、每条规则求值过程、最终结论）；
- 推演记录持久化到独立 simulation_runs 表（与正式规则数据隔离）；
- 可选「AI 推演说明」：LLM 仅翻译结构化链路，不参与推演判定。
"""
from __future__ import annotations

import json

import streamlit as st

from llm_layer.claude_client import LLMError
from llm_layer.explainer import explain_simulation
from rule_engine.sandbox_simulator import SimulationResult, parse_inputs, simulate
from storage.db import get_db
from utils import config


def _render_result(result: SimulationResult) -> None:
    """渲染推演链路与最终结论。

    入参:
        result: SimulationResult 推演结果。

    出参: None。
    """
    st.subheader("推演链路（按优先级顺序）")
    for s in result.steps:
        if not s.evaluated:
            st.caption(f"⏭️ {s.rule_name}（{s.rule_id}）：{s.note}")
        elif s.matched:
            st.success(
                f"✅ {s.rule_name}（优先级 {s.priority}）{s.note}　"
                f"求值：{s.condition_detail}　输出：{s.actions}"
            )
        else:
            st.caption(f"❌ {s.rule_name}（优先级 {s.priority}）：{s.condition_detail}")

    st.subheader("最终输出结论")
    if result.hit_rules:
        top = result.hit_rules[0]
        st.info(f"「{top.rule_name}」按优先级最先命中并生效，输出：{result.final_actions}")
    else:
        st.info("无任何规则命中。")

    # LLM 仅翻译结构化链路，不参与推演判定
    if st.button("🤖 AI 推演说明"):
        if not config.has_api_key():
            st.error("未配置 API Key，无法生成说明。")
        else:
            with st.spinner("正在生成推演说明…"):
                try:
                    text = explain_simulation(result)
                except LLMError as e:
                    st.error(f"推演说明生成失败：{e}")
                else:
                    st.info(text)


def render() -> None:
    """渲染沙箱仿真页。出参: None。"""
    st.header("⑥ 沙箱仿真推演")
    st.caption("隔离沙箱：推演只读规则做内存计算，绝不修改正式规则数据；推演记录存独立表。")

    db = get_db()
    rules = db.get_all_rules()
    if not rules:
        st.info("库内暂无规则。")
        return

    st.markdown("**输入测试样本**（每行一个 `字段=值`，示例：）")
    st.code("order.amount=1500\ncustomer.level=VIP", language=None)
    sample = st.text_area(
        "样本输入",
        height=100,
        placeholder="order.amount=1500\ncustomer.level=VIP",
        key="sandbox_sample",
    )

    def _fill_sample() -> None:
        """填入示例样本（on_click 回调：在组件实例化前写入 session，合法）。"""
        st.session_state["sandbox_sample"] = "order.amount=1500\ncustomer.level=VIP"

    col_fill, col_run, _ = st.columns([1, 1, 3])
    with col_fill:
        st.button("✨ 填入示例样本", on_click=_fill_sample)
    with col_run:
        run_clicked = st.button("🚀 一键推演")

    if run_clicked:
        try:
            inputs = parse_inputs(sample)
        except ValueError as e:
            st.error(f"样本格式错误：{e}")
        else:
            if not inputs:
                # 拦截空输入，避免「0 命中」的误导结果
                st.error(
                    "⚠️ 样本输入为空，无法推演。请按示例填写「字段=值」（每行一个），"
                    "或点击「✨ 填入示例样本」后再推演。"
                )
            else:
                result = simulate(inputs, rules)
                st.session_state["sim_result"] = result
                # 推演记录持久化（独立表，与正式规则数据隔离）
                db.save_simulation(
                    inputs,
                    json.dumps(result.to_dict(), ensure_ascii=False),
                    result.hit_count,
                )
                st.success(f"推演完成，已保存记录（命中 {result.hit_count} 条规则）")

    if "sim_result" in st.session_state:
        _render_result(st.session_state["sim_result"])

    # ------------------------------------------------------------------
    # 推演历史（持久化验证：刷新不丢失）
    # ------------------------------------------------------------------
    st.divider()
    st.subheader("推演历史")
    records = db.list_simulations(limit=10)
    if not records:
        st.caption("暂无推演记录。")
    for rec in records:
        with st.expander(f"#{rec['id']}　{rec['created_at']}　命中 {rec['hit_count']} 条"):
            data = json.loads(rec["result_json"])
            st.markdown(f"**输入样本**：{data['inputs']}")
            for s in data["hit_rules"]:
                st.success(f"命中：{s['rule_name']}（优先级 {s['priority']}）→ {s['actions']}")
            if data["final_actions"]:
                st.markdown(f"**最终结论**：{data['final_actions']}")
            else:
                st.markdown("**最终结论**：无命中")
