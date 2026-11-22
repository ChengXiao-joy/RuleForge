"""SQLite 存储层。

职责：规则持久化（rules 表）与解析日志（parse_logs 表）。
使用标准库 sqlite3，无需外部服务，保证本地一键启动。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from rule_engine.schema import Conflict, Rule, now_iso
from storage import rule_version
from utils import config
from utils.logger import get_logger

log = get_logger("storage.db")

# 建表语句：rules 存规则元数据 + 完整 JSON；parse_logs 存解析过程（用于验收统计）
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rules (
    rule_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    priority    INTEGER NOT NULL DEFAULT 50,
    status      TEXT NOT NULL DEFAULT 'enabled',
    category    TEXT NOT NULL DEFAULT '其他',
    payload     TEXT NOT NULL,
    created_at  TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS parse_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    input_text  TEXT NOT NULL,
    success     INTEGER NOT NULL,
    attempts    INTEGER NOT NULL,
    result_type TEXT,
    error       TEXT,
    created_at  TEXT
);

-- 冲突记录表（阶段2）：每次批量扫描时快照式重写
CREATE TABLE IF NOT EXISTS conflicts (
    conflict_id   TEXT PRIMARY KEY,
    rule_a_id     TEXT NOT NULL,
    rule_b_id     TEXT NOT NULL,
    conflict_type TEXT NOT NULL,
    severity      TEXT NOT NULL,
    field         TEXT,
    description   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'open',
    detected_at   TEXT
);

-- 扫描时被过滤的坏规则（自检不合格，不参与多规则比对）
CREATE TABLE IF NOT EXISTS scan_skipped (
    rule_id    TEXT PRIMARY KEY,
    reason     TEXT NOT NULL,
    skipped_at TEXT
);

-- 沙箱推演记录表（阶段4）：独立存储，与正式规则数据完全隔离
CREATE TABLE IF NOT EXISTS simulation_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    inputs_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    hit_count   INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
"""


class Database:
    """SQLite 数据库访问对象。

    入参:
        db_path: 数据库文件路径；为空时读取 config.DB_PATH。
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else config.DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        """建立数据库连接。出参: sqlite3.Connection。"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        """初始化表结构（幂等）。出参: None。"""
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            rule_version.ensure_schema(conn)  # 版本快照表（阶段3，独立新表）
        log.info("数据库已就绪：%s", self.db_path)

    # ------------------------------------------------------------------
    # 规则 CRUD
    # ------------------------------------------------------------------
    def _upsert_conn(self, conn, rule: Rule) -> None:
        """在给定连接上写入/更新规则（upsert_rule 的内部实现）。

        入参:
            conn: sqlite3 连接。
            rule: Rule 实例。

        出参: None。
        """
        payload = rule.model_dump_json()
        conn.execute(
            """
            INSERT INTO rules (rule_id, name, version, priority, status, category,
                               payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rule_id) DO UPDATE SET
                name=excluded.name, version=excluded.version,
                priority=excluded.priority, status=excluded.status,
                category=excluded.category, payload=excluded.payload,
                updated_at=excluded.updated_at
            """,
            (
                rule.rule_id, rule.name, rule.version, rule.priority,
                rule.status, rule.category, payload,
                rule.created_at, rule.updated_at,
            ),
        )

    def upsert_rule(self, rule: Rule) -> str:
        """写入/更新一条规则（以 rule_id 为准，不生成版本快照）。

        入参:
            rule: Rule 实例。

        出参:
            str: 规则 rule_id。
        """
        with self._connect() as conn:
            self._upsert_conn(conn, rule)
        log.info("规则已保存：%s", rule.rule_id)
        return rule.rule_id

    def create_rule(self, rule: Rule, note: str = "") -> str:
        """新增一条规则（rule_id 已存在则抛 ValueError），并生成首条版本快照。

        入参:
            rule: Rule 实例（rule_id 需唯一）。
            note: 创建备注（为空时记「初始创建」）。

        出参:
            str: 规则 rule_id。

        异常:
            ValueError: 同 rule_id 规则已存在。
        """
        with self._connect() as conn:
            if conn.execute(
                "SELECT 1 FROM rules WHERE rule_id = ?", (rule.rule_id,)
            ).fetchone():
                raise ValueError(f"规则 {rule.rule_id} 已存在，请使用更新操作或换一个 ID。")
            self._upsert_conn(conn, rule)
            # 版本快照：初始版本，修改前 JSON 为空
            rule_version.insert_snapshot(
                conn,
                rule.rule_id,
                rule.version,
                before_json=None,
                after_json=rule.model_dump_json(),
                note=note or "初始创建",
            )
        log.info("规则已创建并生成初始快照：%s", rule.rule_id)
        return rule.rule_id

    def update_rule(self, rule: Rule, note: str = "") -> Rule | None:
        """更新一条规则（不存在返回 None），版本号自动 +1、更新时间刷新，
        并自动生成版本快照（记录修改前/修改后完整 JSON）。

        入参:
            rule: Rule 实例（含要更新的字段；rule_id 用于定位，version 会被忽略并自动递增）。
            note: 修改人备注。

        出参:
            Rule | None: 更新后的规则；规则不存在返回 None。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM rules WHERE rule_id = ?", (rule.rule_id,)
            ).fetchone()
            if row is None:
                return None
            existing = Rule.model_validate_json(row["payload"])
            data = rule.model_dump()
            data["version"] = existing.version + 1
            data["updated_at"] = now_iso()
            updated = Rule.model_validate(data)  # 确保落库前通过 Schema 校验
            self._upsert_conn(conn, updated)
            # 版本快照：修改前 = 旧规则完整 JSON，修改后 = 新规则完整 JSON
            rule_version.insert_snapshot(
                conn,
                rule.rule_id,
                updated.version,
                before_json=existing.model_dump_json(),
                after_json=updated.model_dump_json(),
                note=note,
            )
        return updated

    def get_rule(self, rule_id: str) -> Rule | None:
        """按 ID 读取规则。

        入参:
            rule_id: 规则 ID。

        出参:
            Rule | None: 反序列化后的规则；不存在返回 None。
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM rules WHERE rule_id = ?", (rule_id,)
            ).fetchone()
        if row is None:
            return None
        return Rule.model_validate_json(row["payload"])

    def list_rules(self) -> list[dict]:
        """列出全部规则元数据（不含 payload）。

        出参:
            list[dict]: 每项含 rule_id/name/version/priority/status/category/created_at/updated_at。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT rule_id, name, version, priority, status, category, "
                "created_at, updated_at FROM rules ORDER BY priority DESC, created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_rules(self) -> list[Rule]:
        """读取库内全部规则（完整对象）。

        出参:
            list[Rule]: 全部规则列表。
        """
        with self._connect() as conn:
            rows = conn.execute("SELECT payload FROM rules ORDER BY priority DESC").fetchall()
        return [Rule.model_validate_json(r["payload"]) for r in rows]

    def delete_rule(self, rule_id: str) -> bool:
        """删除规则。

        入参:
            rule_id: 规则 ID。

        出参:
            bool: True 表示删除成功；False 表示记录不存在。
        """
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM rules WHERE rule_id = ?", (rule_id,))
            deleted = cur.rowcount > 0
            if deleted:
                # 阶段3：同步归档该规则全部历史版本（软删除标记，不物理删除）
                rule_version.archive_versions(conn, rule_id)
                # 一致性：清除涉及该规则的冲突记录与被过滤标注，避免报告残留失效数据
                conn.execute(
                    "DELETE FROM conflicts WHERE rule_a_id = ? OR rule_b_id = ?",
                    (rule_id, rule_id),
                )
                conn.execute("DELETE FROM scan_skipped WHERE rule_id = ?", (rule_id,))
        log.info("删除规则 %s：%s", rule_id, "成功" if deleted else "不存在")
        return deleted

    # ------------------------------------------------------------------
    # 规则版本快照（阶段3）
    # ------------------------------------------------------------------
    def list_rule_versions(self, rule_id: str, include_archived: bool = False) -> list[dict]:
        """读取某条规则的版本快照列表（按版本号降序）。

        入参:
            rule_id:          规则 ID。
            include_archived: 是否包含已归档（软删除）的版本。

        出参:
            list[dict]: 快照记录列表。
        """
        with self._connect() as conn:
            return rule_version.list_versions(conn, rule_id, include_archived)

    def restore_versions(self, rule_id: str, versions: list[dict]) -> int:
        """重建导入规则的版本快照（批量导入专用，不改动表结构）。

        按版本号升序重建：before_json 取前一个版本的 after_json（首版为 None）。

        入参:
            rule_id:  规则 ID。
            versions: 版本快照基础信息列表，每项含 version/note/tag/created_at/after_json。

        出参:
            int: 重建的快照条数。
        """
        import json

        with self._connect() as conn:
            conn.execute("DELETE FROM rule_versions WHERE rule_id = ?", (rule_id,))
            prev_after: str | None = None
            count = 0
            for v in sorted(versions, key=lambda x: int(x.get("version", 1))):
                after_str = json.dumps(v.get("after_json", {}), ensure_ascii=False)
                rule_version.insert_snapshot(
                    conn,
                    rule_id,
                    int(v.get("version", 1)),
                    before_json=prev_after,
                    after_json=after_str,
                    note=v.get("note", ""),
                    created_at=v.get("created_at"),
                    tag=v.get("tag", ""),
                )
                prev_after = after_str
                count += 1
        log.info("规则 %s 重建版本快照 %d 条", rule_id, count)
        return count

    def dashboard_stats(self) -> dict:
        """数据看板统计（全部自研 SQL/计算，与库内数据实时一致）。

        出参:
            dict: {
                "rules":           规则总数,
                "enabled":         启用规则数,
                "disabled":        禁用规则数,
                "draft":           草稿规则数,
                "bad_rules":       单规则自检不合格的规则数,
                "conflicts":       冲突对数量,
                "versions":        版本快照总数量（未归档）,
                "simulations":     沙箱推演次数,
            }
        """
        from rule_engine.single_rule_checker import check_rule

        with self._connect() as conn:
            rules = conn.execute("SELECT COUNT(*) AS n FROM rules").fetchone()["n"]
            conflicts = conn.execute("SELECT COUNT(*) AS n FROM conflicts").fetchone()["n"]
            versions = conn.execute(
                "SELECT COUNT(*) AS n FROM rule_versions WHERE archived = 0"
            ).fetchone()["n"]
            simulations = conn.execute("SELECT COUNT(*) AS n FROM simulation_runs").fetchone()["n"]
            enabled = conn.execute(
                "SELECT COUNT(*) AS n FROM rules WHERE status = 'enabled'"
            ).fetchone()["n"]
            disabled = conn.execute(
                "SELECT COUNT(*) AS n FROM rules WHERE status = 'disabled'"
            ).fetchone()["n"]
            draft = conn.execute(
                "SELECT COUNT(*) AS n FROM rules WHERE status = 'draft'"
            ).fetchone()["n"]

        # 自检异常数：与「单规则自检」同口径，对每条规则实跑 check_rule
        bad = 0
        for r in self.get_all_rules():
            if not check_rule(r).ok:
                bad += 1

        return {
            "rules": rules,
            "enabled": enabled,
            "disabled": disabled,
            "draft": draft,
            "bad_rules": bad,
            "conflicts": conflicts,
            "versions": versions,
            "simulations": simulations,
        }

    def version_activity_7d(self) -> list[dict]:
        """近7天每天生成的版本快照数量（按本地时间统计，含归档）。

        出参:
            list[dict]: 每项 {"日期": "YYYY-MM-DD", "版本快照数": int}，按日期升序共 7 天。
        """
        from datetime import datetime, timedelta

        today = datetime.now().date()
        start = today - timedelta(days=6)
        with self._connect() as conn:
            rows = conn.execute("SELECT created_at FROM rule_versions").fetchall()

        counts: dict = {}
        for r in rows:
            try:
                d = datetime.strptime((r["created_at"] or "")[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            if start <= d <= today:
                counts[d] = counts.get(d, 0) + 1

        return [
            {"日期": str(start + timedelta(days=i)), "版本快照数": counts.get(start + timedelta(days=i), 0)}
            for i in range(7)
        ]

    def clear_rule_versions(self, rule_id: str) -> int:
        """清空某条规则的全部版本快照（高危操作：物理删除，调用方需二次确认）。

        入参:
            rule_id: 规则 ID。

        出参:
            int: 被删除的快照条数。
        """
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM rule_versions WHERE rule_id = ?", (rule_id,))
        n = cur.rowcount
        log.info("规则 %s 的 %d 条版本快照已清空", rule_id, n)
        return n

    def set_version_tag(self, rule_id: str, version: int, tag: str) -> bool:
        """给规则指定版本打标签（如 v1.0 / 上线版 / 草稿）。

        入参:
            rule_id: 规则 ID。
            version: 版本号。
            tag:     标签文本。

        出参:
            bool: True 表示成功；False 表示该版本快照不存在。
        """
        with self._connect() as conn:
            return rule_version.set_tag(conn, rule_id, version, tag)

    # ------------------------------------------------------------------
    # 冲突记录（阶段2）
    # ------------------------------------------------------------------
    def save_scan_result(
        self,
        conflicts: list[Conflict],
        skipped: list[tuple[str, str]],
    ) -> None:
        """保存一次冲突扫描结果（快照式：清空旧冲突与旧坏规则记录后写入）。

        入参:
            conflicts: 本次扫描识别到的冲突列表。
            skipped:   被过滤的坏规则列表，每项为 (rule_id, 过滤原因)。

        出参: None。
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM conflicts")
            conn.execute("DELETE FROM scan_skipped")
            for c in conflicts:
                conn.execute(
                    "INSERT INTO conflicts (conflict_id, rule_a_id, rule_b_id, conflict_type, "
                    "severity, field, description, status, detected_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        c.conflict_id, c.rule_a_id, c.rule_b_id, c.conflict_type,
                        c.severity, c.field, c.description, c.status, c.detected_at,
                    ),
                )
            for rule_id, reason in skipped:
                conn.execute(
                    "INSERT INTO scan_skipped (rule_id, reason, skipped_at) VALUES (?,?,?)",
                    (rule_id, reason, now_iso()),
                )
        log.info("冲突扫描结果已保存：%d 个冲突，%d 条坏规则被过滤", len(conflicts), len(skipped))

    def list_conflicts(self) -> list[dict]:
        """读取全部冲突记录（按严重度、检测时间排序）。

        出参:
            list[dict]: 冲突记录列表。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM conflicts ORDER BY "
                "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, detected_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_skipped(self) -> list[dict]:
        """读取被过滤的坏规则记录。

        出参:
            list[dict]: 坏规则记录列表（rule_id / reason / skipped_at）。
        """
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM scan_skipped").fetchall()
        return [dict(r) for r in rows]

    def clear_scan_result(self) -> None:
        """清空冲突记录与坏规则记录。

        出参: None。
        """
        with self._connect() as conn:
            conn.execute("DELETE FROM conflicts")
            conn.execute("DELETE FROM scan_skipped")
        log.info("冲突扫描结果已清空")

    # ------------------------------------------------------------------
    # 沙箱推演记录（阶段4，独立表，与正式规则数据隔离）
    # ------------------------------------------------------------------
    def save_simulation(self, inputs: dict, result_json: str, hit_count: int) -> int:
        """保存一次沙箱推演记录（只写 simulation_runs 表，不动任何规则数据）。

        入参:
            inputs:      输入样本 dict。
            result_json: 完整推演结果 JSON（SimulationResult.to_dict 序列化）。
            hit_count:   命中规则数。

        出参:
            int: 推演记录自增 ID。
        """
        import json

        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO simulation_runs (inputs_json, result_json, hit_count, created_at) "
                "VALUES (?, ?, ?, ?)",
                (json.dumps(inputs, ensure_ascii=False), result_json, hit_count, now_iso()),
            )
        log.info("沙箱推演记录已保存：%d 条命中", hit_count)
        return cur.lastrowid

    def list_simulations(self, limit: int = 20) -> list[dict]:
        """读取最近若干次推演记录（按时间倒序）。

        入参:
            limit: 最多返回条数。

        出参:
            list[dict]: 记录列表，每项含 id/inputs_json/result_json/hit_count/created_at。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM simulation_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # 解析日志（用于阶段0 验收统计）
    # ------------------------------------------------------------------
    def log_parse(
        self,
        input_text: str,
        success: bool,
        attempts: int,
        result_type: str | None,
        error: str | None = None,
    ) -> None:
        """记录一次解析过程。

        入参:
            input_text:  用户原始输入。
            success:     是否成功。
            attempts:    成功时所用尝试次数。
            result_type: rule / clarification / None。
            error:       失败原因（成功时为 None）。

        出参: None。
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO parse_logs (input_text, success, attempts, result_type, error, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (input_text, int(success), attempts, result_type, error, now_iso()),
            )

    def parse_stats(self) -> dict:
        """统计解析成功率。

        出参:
            dict: {"total", "success", "first_attempt", "rate"}。
        """
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS n FROM parse_logs").fetchone()["n"]
            success = conn.execute(
                "SELECT COUNT(*) AS n FROM parse_logs WHERE success = 1"
            ).fetchone()["n"]
            first = conn.execute(
                "SELECT COUNT(*) AS n FROM parse_logs WHERE success = 1 AND attempts = 1"
            ).fetchone()["n"]
        return {
            "total": total,
            "success": success,
            "first_attempt": first,
            "rate": round(success / total, 4) if total else 0.0,
        }


# 模块级单例（懒加载）
_db: Database | None = None


def get_db() -> Database:
    """获取全局数据库单例。

    出参:
        Database: 共享的数据库实例。
    """
    global _db
    if _db is None:
        _db = Database()
    return _db
