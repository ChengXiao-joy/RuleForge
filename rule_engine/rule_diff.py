"""规则版本 Diff 工具（rule_diff）。

对两个版本的规则 JSON 做结构化差异比对，区分三类差异：
- added   新增字段（含新增列表元素、新增子树）
- removed 删除字段
- changed 字段值变更（标量值不同）

比对逻辑全部自研（递归树比对），LLM 不参与比对；LLM 仅负责把本模块输出的
结构化 diff 结果翻译成通俗中文变更说明（见 llm_layer/explainer.explain_diff）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DiffEntry:
    """一条差异记录。

    字段:
        kind: 差异类型，added / removed / changed。
        path: 点分路径定位（如 conditions.children[0].value）。
        old:  旧值（changed/removed 时有意义）。
        new:  新值（changed/added 时有意义）。
    """
    kind: str
    path: str
    old: Any = None
    new: Any = None

    def to_dict(self) -> dict:
        """转为可序列化 dict。出参: dict。"""
        return {"kind": self.kind, "path": self.path, "old": self.old, "new": self.new}


@dataclass
class DiffResult:
    """一次比对的完整结果。字段: entries 差异列表。"""
    entries: list[DiffEntry] = field(default_factory=list)

    @property
    def added(self) -> list[DiffEntry]:
        """新增差异列表。出参: list[DiffEntry]。"""
        return [e for e in self.entries if e.kind == "added"]

    @property
    def removed(self) -> list[DiffEntry]:
        """删除差异列表。出参: list[DiffEntry]。"""
        return [e for e in self.entries if e.kind == "removed"]

    @property
    def changed(self) -> list[DiffEntry]:
        """值变更差异列表。出参: list[DiffEntry]。"""
        return [e for e in self.entries if e.kind == "changed"]

    @property
    def is_empty(self) -> bool:
        """是否无差异。出参: bool。"""
        return not self.entries

    def summary_text(self) -> str:
        """生成结构化摘要文本（供 LLM 翻译与页面展示）。

        出参:
            str: 每行一条差异，形如：
                [新增] actions[0].params.discount: 0.8
                [删除] description: 旧说明
                [变更] priority: 50 -> 60
        """
        if self.is_empty:
            return "（两个版本完全一致，无任何差异）"
        lines = []
        for e in self.entries:
            if e.kind == "added":
                lines.append(f"[新增] {e.path}: {_fmt(e.new)}")
            elif e.kind == "removed":
                lines.append(f"[删除] {e.path}: {_fmt(e.old)}")
            else:
                lines.append(f"[变更] {e.path}: {_fmt(e.old)} -> {_fmt(e.new)}")
        return "\n".join(lines)


def _fmt(v: Any) -> str:
    """把值格式化为短文本。入参: v 任意值。出参: str（截断防超长）。"""
    s = str(v)
    return s if len(s) <= 120 else s[:117] + "..."


def _path_join(parent: str, key: str | int) -> str:
    """拼接点分路径。入参: parent 父路径；key 键。出参: str。"""
    if not parent:
        return str(key)
    return f"{parent}.{key}" if not str(key).startswith("[") else f"{parent}{key}"


def diff_dicts(old: Any, new: Any, path: str = "") -> DiffResult:
    """递归比对两个 JSON 结构，产出结构化差异。

    入参:
        old:  旧版本 JSON（dict / list / 标量）。
        new:  新版本 JSON。
        path: 当前点分路径（递归内部使用）。

    出参:
        DiffResult: 差异结果。
    """
    result = DiffResult()

    if isinstance(old, dict) and isinstance(new, dict):
        keys = list(new.keys()) + [k for k in old.keys() if k not in new]
        for k in keys:
            child_path = _path_join(path, k)
            if k not in old:
                result.entries.append(DiffEntry("added", child_path, old=None, new=new[k]))
            elif k not in new:
                result.entries.append(DiffEntry("removed", child_path, old=old[k], new=None))
            else:
                result.entries.extend(diff_dicts(old[k], new[k], child_path).entries)

    elif isinstance(old, list) and isinstance(new, list):
        n = max(len(old), len(new))
        for i in range(n):
            child_path = _path_join(path, f"[{i}]")
            if i >= len(old):
                result.entries.append(DiffEntry("added", child_path, old=None, new=new[i]))
            elif i >= len(new):
                result.entries.append(DiffEntry("removed", child_path, old=old[i], new=None))
            else:
                result.entries.extend(diff_dicts(old[i], new[i], child_path).entries)

    else:
        # 标量（或类型不同）：不相等即值变更
        if old != new:
            result.entries.append(DiffEntry("changed", path or "（根）", old=old, new=new))

    return result


# 自动管理、不具业务含义的元数据字段（版本对比时默认忽略，避免噪音）
_META_PATHS = ("version", "created_at", "updated_at")


def diff_versions(version_a: dict, version_b: dict, ignore_meta: bool = True) -> DiffResult:
    """比对两个版本快照的完整 JSON（版本A 为旧、版本B 为新）。

    入参:
        version_a:   旧版本完整 JSON dict。
        version_b:   新版本完整 JSON dict。
        ignore_meta: 是否忽略 version/created_at/updated_at 等自动管理字段（默认忽略）。

    出参:
        DiffResult: 结构化差异结果。
    """
    result = diff_dicts(version_a, version_b)
    if ignore_meta:
        result.entries = [e for e in result.entries if e.path not in _META_PATHS]
    return result
