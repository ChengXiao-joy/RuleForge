"""规则版本快照模块（storage/rule_version.py）。

职责：规则版本快照的生成、查询、打标签、软删除归档。

设计约定：
- 版本表 rule_versions 独立新增，不改动阶段0 冻结的 rules / conflicts 表结构；
- 每次新增/编辑保存规则时由 storage/db.py 自动调用本模块生成快照；
- 删除规则时对版本数据做软删除（archived=1），不物理删除；
- 本模块不 import db.py，避免循环依赖；所有函数接收 sqlite3.Connection。
"""
from __future__ import annotations

import sqlite3

from utils import config
from utils.logger import get_logger

log = get_logger("storage.rule_version")

# 版本记录表：不改动原有表结构，独立新增
VERSION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rule_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id     TEXT NOT NULL,
    version     INTEGER NOT NULL,
    before_json TEXT,
    after_json  TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    tag         TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    archived    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rule_versions_rule ON rule_versions(rule_id, version);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """确保版本表存在（幂等）。

    入参:
        conn: sqlite3 连接。

    出参: None。
    """
    conn.executescript(VERSION_SCHEMA_SQL)


def insert_snapshot(
    conn: sqlite3.Connection,
    rule_id: str,
    version: int,
    before_json: str | None,
    after_json: str,
    note: str = "",
    created_at: str | None = None,
    tag: str = "",
) -> None:
    """写入一条版本快照。

    入参:
        conn:        sqlite3 连接（由调用方事务管理）。
        rule_id:     规则 ID。
        version:     版本号（与规则当前 version 一致）。
        before_json: 修改前完整 JSON（创建首版时为 None）。
        after_json:  修改后完整 JSON。
        note:        修改人备注。
        created_at:  快照时间；为空时取当前北京时间。
        tag:         版本标签（导入重建时使用，默认空）。

    出参: None。
    """
    from rule_engine.schema import now_iso

    conn.execute(
        "INSERT INTO rule_versions (rule_id, version, before_json, after_json, note, tag, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (rule_id, version, before_json, after_json, note, tag, created_at or now_iso()),
    )


def list_versions(
    conn: sqlite3.Connection,
    rule_id: str,
    include_archived: bool = False,
) -> list[dict]:
    """读取某条规则的版本快照列表（按版本号降序）。

    入参:
        conn:             sqlite3 连接。
        rule_id:          规则 ID。
        include_archived: 是否包含已归档（软删除）的版本。

    出参:
        list[dict]: 快照记录列表，每项含 id/rule_id/version/before_json/after_json/
                    note/tag/created_at/archived。
    """
    sql = "SELECT * FROM rule_versions WHERE rule_id = ?"
    params: list = [rule_id]
    if not include_archived:
        sql += " AND archived = 0"
    sql += " ORDER BY version DESC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_version(conn: sqlite3.Connection, rule_id: str, version: int) -> dict | None:
    """读取指定版本快照。

    入参:
        conn:    sqlite3 连接。
        rule_id: 规则 ID。
        version: 版本号。

    出参:
        dict | None: 快照记录；不存在返回 None。
    """
    row = conn.execute(
        "SELECT * FROM rule_versions WHERE rule_id = ? AND version = ?",
        (rule_id, version),
    ).fetchone()
    return dict(row) if row else None


def set_tag(conn: sqlite3.Connection, rule_id: str, version: int, tag: str) -> bool:
    """给指定版本打标签（如 v1.0 / 上线版 / 草稿）。不打新快照，只更新标签。

    入参:
        conn:    sqlite3 连接。
        rule_id: 规则 ID。
        version: 版本号。
        tag:     标签文本。

    出参:
        bool: True 表示更新成功；False 表示该版本快照不存在。
    """
    cur = conn.execute(
        "UPDATE rule_versions SET tag = ? WHERE rule_id = ? AND version = ?",
        (tag, rule_id, version),
    )
    ok = cur.rowcount > 0
    if ok:
        log.info("规则 %s v%s 已打标签：%s", rule_id, version, tag)
    return ok


def archive_versions(conn: sqlite3.Connection, rule_id: str) -> int:
    """软删除归档某条规则的全部版本快照（archived=1，不物理删除）。

    入参:
        conn:    sqlite3 连接。
        rule_id: 规则 ID。

    出参:
        int: 被归档的版本数量。
    """
    cur = conn.execute(
        "UPDATE rule_versions SET archived = 1 WHERE rule_id = ?", (rule_id,)
    )
    n = cur.rowcount
    log.info("规则 %s 的 %d 条版本快照已软删除归档", rule_id, n)
    return n
