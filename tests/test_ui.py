"""Streamlit UI 冒烟测试。

用 Streamlit 官方 AppTest 无头运行 app，切换三个页面，验证均不抛异常。
不依赖浏览器，离线可运行。

注意：本测试会模拟 `streamlit run ui/app.py` 的真实搜索路径 ——
sys.path[0] 为 ui/ 目录且不含项目根目录，以回归验证 app.py 顶部的路径引导。
"""
import sys
from pathlib import Path

import pytest

ROOT = str(Path(__file__).resolve().parent.parent)
APP = str(Path(ROOT) / "ui" / "app.py")

pytest.importorskip("streamlit")


@pytest.fixture(autouse=True)
def _restore_syspath():
    """每个测试结束后恢复 sys.path，避免测试间互相污染。"""
    orig = list(sys.path)
    yield
    sys.path[:] = orig


def test_app_smoke_all_pages():
    """依次渲染新增页 / 列表页 / 详情页，断言无异常。"""
    # 模拟 streamlit run ui/app.py：剔除项目根目录，把 ui/ 放到搜索路径首位
    sys.path = [p for p in sys.path if Path(p or ".").resolve() != Path(ROOT)]
    sys.path.insert(0, str(Path(ROOT) / "ui"))

    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(APP, default_timeout=60)

    at.run()
    assert not at.exception, f"新增页异常：{at.exception}"

    at.session_state["nav"] = "规则列表"
    at.run()
    assert not at.exception, f"列表页异常：{at.exception}"

    at.session_state["nav"] = "规则详情/编辑"
    at.run()
    assert not at.exception, f"详情页异常：{at.exception}"

    at.session_state["nav"] = "冲突报告"
    at.run()
    assert not at.exception, f"冲突报告页异常：{at.exception}"

    at.session_state["nav"] = "版本历史"
    at.run()
    assert not at.exception, f"版本历史页异常：{at.exception}"

    at.session_state["nav"] = "沙箱仿真"
    at.run()
    assert not at.exception, f"沙箱仿真页异常：{at.exception}"

    at.session_state["nav"] = "批量导入导出"
    at.run()
    assert not at.exception, f"批量导入导出页异常：{at.exception}"


def test_edit_save_triggers_self_check(tmp_path, monkeypatch):
    """端到端验证联动逻辑：详情页编辑保存后，自动触发单规则自检。

    模拟真实点击流：详情页 → 点「编辑此规则」→ 修改名称 → 提交「保存修改」，
    然后断言：自检结果已执行并写入会话、版本递增 v2、页面展示「自动自检」提示。
    """
    from streamlit.testing.v1 import AppTest

    import storage.db as dbmod
    from rule_engine import schema

    # 使用独立临时数据库，避免污染真实数据
    test_db = dbmod.Database(tmp_path / "ui.db")
    monkeypatch.setattr(dbmod, "_db", test_db)

    rid = "R_UI_EDIT"
    test_db.create_rule(
        schema.Rule(
            rule_id=rid,
            name="待编辑规则",
            category="其他",
            conditions=schema.ConditionLeaf(field="x", operator="gt", value=1),
            actions=[schema.Action(action_type="notify", target="t")],
        )
    )

    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["nav"] = "规则详情/编辑"
    at.session_state["selected_rule_id"] = rid
    at.run()
    assert not at.exception, at.exception

    # 1) 点击「编辑此规则」，展开编辑表单
    edit_btn = next(b for b in at.button if b.label == "✏️ 编辑此规则")
    edit_btn.click()
    at.run()
    assert not at.exception, at.exception

    # 2) 修改名称并提交表单
    at.text_input[0].set_value("改名后的规则")
    submit_btn = next(b for b in at.button if b.label == "保存修改")
    submit_btn.click()
    at.run()
    assert not at.exception, at.exception

    # 3) 断言：保存后自动自检确实执行了
    assert at.session_state["edited_rule_id"] == rid
    assert at.session_state["edited_check"] is not None, "保存后未触发单规则自检！"
    assert at.session_state["edited_check_time"], "自检时间未记录"
    # 版本递增到 v2、名称已更新
    saved = test_db.get_rule(rid)
    assert saved.version == 2
    assert saved.name == "改名后的规则"
    # 页面顶部展示「保存 + 自动自检」提示，自检区标注「保存后自动执行」
    assert any("自动自检" in s.value for s in at.success), [s.value for s in at.success]
    assert any("自动执行" in c.value for c in at.caption), [c.value for c in at.caption]


def test_detail_delete_requires_confirmation(tmp_path, monkeypatch):
    """详情页删除必须有二次确认：点删除不生效，点确认才真正删除。"""
    from streamlit.testing.v1 import AppTest

    import storage.db as dbmod
    from rule_engine import schema

    test_db = dbmod.Database(tmp_path / "ui3.db")
    monkeypatch.setattr(dbmod, "_db", test_db)
    rid = "R_DEL_1"
    test_db.create_rule(
        schema.Rule(
            rule_id=rid,
            name="待删规则",
            conditions=schema.ConditionLeaf(field="x", operator="gt", value=1),
            actions=[schema.Action(action_type="notify", target="t")],
        )
    )

    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["nav"] = "规则详情/编辑"
    at.session_state["selected_rule_id"] = rid
    at.run()
    assert not at.exception, at.exception

    # 1) 点「删除此规则」→ 仅出现确认提示，规则尚未删除
    del_btn = next(b for b in at.button if b.label == "🗑️ 删除此规则")
    del_btn.click()
    at.run()
    assert not at.exception, at.exception
    assert test_db.get_rule(rid) is not None, "点删除后不应立即删除，需二次确认"

    # 2) 点「✅ 确认删除」→ 规则真正删除
    confirm_btn = next(b for b in at.button if b.label == "✅ 确认删除")
    confirm_btn.click()
    at.run()
    assert not at.exception, at.exception
    assert test_db.get_rule(rid) is None, "确认后应删除"


def test_sandbox_empty_input_blocked(tmp_path, monkeypatch):
    """沙箱页空样本推演被拦截：给出明确提示，且不落库 0 命中记录。"""
    from streamlit.testing.v1 import AppTest

    import storage.db as dbmod
    from rule_engine import schema

    test_db = dbmod.Database(tmp_path / "ui4.db")
    monkeypatch.setattr(dbmod, "_db", test_db)
    test_db.create_rule(
        schema.Rule(
            rule_id="R_SBX",
            name="样例规则",
            conditions=schema.ConditionLeaf(field="x", operator="gt", value=1),
            actions=[schema.Action(action_type="notify", target="t")],
        )
    )

    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["nav"] = "沙箱仿真"
    at.run()
    assert not at.exception, at.exception

    before = len(test_db.list_simulations())
    run_btn = next(b for b in at.button if b.label == "🚀 一键推演")
    run_btn.click()
    at.run()
    assert not at.exception, at.exception
    assert any("为空" in e.value for e in at.error), [e.value for e in at.error]
    assert len(test_db.list_simulations()) == before  # 未落库


def test_sandbox_fill_sample_button(tmp_path, monkeypatch):
    """沙箱页「填入示例样本」按钮：自动填入示例且不抛组件键异常。"""
    from streamlit.testing.v1 import AppTest

    import storage.db as dbmod
    from rule_engine import schema

    test_db = dbmod.Database(tmp_path / "ui5.db")
    monkeypatch.setattr(dbmod, "_db", test_db)
    test_db.create_rule(
        schema.Rule(
            rule_id="R_SBX2",
            name="样例规则",
            conditions=schema.ConditionLeaf(field="x", operator="gt", value=1),
            actions=[schema.Action(action_type="notify", target="t")],
        )
    )

    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["nav"] = "沙箱仿真"
    at.run()
    assert not at.exception, at.exception

    fill_btn = next(b for b in at.button if b.label == "✨ 填入示例样本")
    fill_btn.click()
    at.run()
    assert not at.exception, f"填入示例按钮异常：{at.exception}"
    assert at.session_state["sandbox_sample"] == "order.amount=1500\ncustomer.level=VIP"


def test_rule_list_buttons_navigate(tmp_path, monkeypatch):
    """列表页「查看/编辑」与「批量冲突扫描」按钮点击后正确跳页且无异常。

    回归验证：导航跳转必须走 pending_nav 约定，
    直接改 nav 会触发 StreamlitWidgetAlreadyInstantiatedError。
    """
    from streamlit.testing.v1 import AppTest

    import storage.db as dbmod
    from rule_engine import schema

    test_db = dbmod.Database(tmp_path / "ui2.db")
    monkeypatch.setattr(dbmod, "_db", test_db)
    test_db.create_rule(
        schema.Rule(
            rule_id="R_NAV_1",
            name="规则一",
            conditions=schema.ConditionLeaf(field="x", operator="gt", value=1),
            actions=[schema.Action(action_type="notify", target="t")],
        )
    )

    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["nav"] = "规则列表"
    at.run()
    assert not at.exception, at.exception

    # 1) 查看/编辑 → 跳到详情页
    view_btn = next(b for b in at.button if b.label == "🔍 查看 / 编辑")
    view_btn.click()
    at.run()
    assert not at.exception, f"查看/编辑跳转异常：{at.exception}"
    assert at.session_state["nav"] == "规则详情/编辑"

    # 2) 通过侧边栏 radio 回到列表页
    at.sidebar.radio[0].set_value("规则列表")
    at.run()
    assert not at.exception, at.exception

    # 3) 批量冲突扫描 → 跳到冲突报告页，且扫描结果落库
    scan_btn = next(b for b in at.button if b.label == "🔍 批量冲突扫描")
    scan_btn.click()
    at.run()
    assert not at.exception, f"批量冲突扫描异常：{at.exception}"
    assert at.session_state["nav"] == "冲突报告"
    assert len(test_db.list_conflicts()) == 0  # 单条规则无冲突，但扫描已执行并落库
    assert "flash_scan" not in at.session_state  # 报告页已消费扫描提示
