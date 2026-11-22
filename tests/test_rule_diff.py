"""规则版本 Diff 工具单元测试（离线）。"""
import json

from rule_engine.rule_diff import diff_dicts, diff_versions


def test_changed_scalar():
    r = diff_dicts({"priority": 50}, {"priority": 60})
    assert len(r.entries) == 1
    e = r.changed[0]
    assert e.kind == "changed" and e.path == "priority"
    assert e.old == 50 and e.new == 60


def test_added_and_removed_fields():
    old = {"name": "a", "desc": "x"}
    new = {"name": "a", "note": "y"}
    r = diff_dicts(old, new)
    assert [e.path for e in r.added] == ["note"]
    assert [e.path for e in r.removed] == ["desc"]


def test_nested_change_path():
    old = {"conditions": {"children": [{"field": "x", "operator": "gt", "value": 10}]}}
    new = {"conditions": {"children": [{"field": "x", "operator": "gt", "value": 20}]}}
    r = diff_dicts(old, new)
    assert len(r.entries) == 1
    e = r.entries[0]
    assert e.kind == "changed"
    assert e.path == "conditions.children[0].value"
    assert e.old == 10 and e.new == 20


def test_list_element_added_removed():
    old = {"actions": [{"type": "action", "action_type": "notify"}]}
    new = {"actions": [{"type": "action", "action_type": "notify"}, {"type": "action", "action_type": "tag"}]}
    r = diff_dicts(old, new)
    assert any(e.kind == "added" and e.path == "actions[1]" for e in r.entries)
    r2 = diff_dicts(new, old)
    assert any(e.kind == "removed" and e.path == "actions[1]" for e in r2.entries)


def test_list_element_changed_in_place():
    old = {"actions": [{"action_type": "notify", "target": "hr"}]}
    new = {"actions": [{"action_type": "notify", "target": "boss"}]}
    r = diff_dicts(old, new)
    assert len(r.entries) == 1
    assert r.entries[0].kind == "changed"
    assert r.entries[0].path == "actions[0].target"


def test_no_diff():
    assert diff_dicts({"a": 1, "b": {"c": [1, 2]}}, {"a": 1, "b": {"c": [1, 2]}}).is_empty


def test_diff_versions_rule_edit():
    """真实场景：编辑规则（改优先级 + 条件值 + 备注）的差异识别。"""
    v1 = {
        "rule_id": "R1", "name": "n", "version": 1, "priority": 50,
        "conditions": {"type": "condition", "field": "x", "operator": "gt", "value": 10},
        "actions": [],
    }
    v2 = json.loads(json.dumps(v1))
    v2["priority"] = 80
    v2["conditions"]["value"] = 20
    r = diff_versions(v1, v2)
    paths = [e.path for e in r.entries]
    assert "priority" in paths
    assert "conditions.value" in paths
    assert len(r.changed) == 2


def test_summary_text():
    r = diff_dicts({"a": 1, "b": 2}, {"a": 3, "c": 4})
    text = r.summary_text()
    assert "[变更] a: 1 -> 3" in text
    assert "[删除] b" in text
    assert "[新增] c" in text


def test_diff_versions_ignores_meta_fields():
    """版本对比默认忽略 version/created_at/updated_at 等自动管理字段。"""
    a = {"rule_id": "R", "name": "n", "version": 1, "priority": 50, "created_at": "t1"}
    b = {"rule_id": "R", "name": "n", "version": 2, "priority": 80, "created_at": "t2"}
    r = diff_versions(a, b)
    paths = [e.path for e in r.entries]
    assert "priority" in paths
    assert "version" not in paths and "created_at" not in paths
    # 可关闭忽略
    r2 = diff_versions(a, b, ignore_meta=False)
    paths2 = [e.path for e in r2.entries]
    assert "version" in paths2


def test_empty_summary_text():
    r = diff_dicts({"a": 1}, {"a": 1})
    assert "完全一致" in r.summary_text()
