"""Rule Schema —— 定稿版（阶段0 冻结）。

本模块是规则结构化输出的唯一权威定义。所有层（LLM 翻译、存储、引擎）均以此为准，
后续开发【禁止】私自新增、修改字段；如确需演进，必须先更新 docs/rule_schema.md 并全量回归。

关键约束：
- 所有模型配置 extra="forbid"，杜绝 LLM 或调用方塞入未定义字段。
- 条件树为「逻辑节点 + 条件叶子节点」的判别联合（靠 type 字段区分）。
- 冲突结构体 Conflict 在阶段0 一并冻结，供阶段1 冲突检测引擎使用。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# 冻结的枚举值集合
# ---------------------------------------------------------------------------
# 条件操作符
CONDITION_OPERATORS = (
    "eq", "ne", "gt", "gte", "lt", "lte",
    "contains", "not_contains", "in", "not_in",
    "between", "empty", "not_empty",
)
Operator = Literal[
    "eq", "ne", "gt", "gte", "lt", "lte",
    "contains", "not_contains", "in", "not_in",
    "between", "empty", "not_empty",
]

# 动作类型
ACTION_TYPES = ("notify", "approve", "tag", "set_field", "create_task", "block")
ActionType = Literal["notify", "approve", "tag", "set_field", "create_task", "block"]

# 规则状态
RuleStatus = Literal["enabled", "disabled", "draft"]

# 冲突类型 / 严重度 / 处理状态（阶段1 使用，阶段0 冻结结构）
ConflictType = Literal["action_conflict", "priority_conflict", "condition_overlap", "mutual_exclusion"]
ConflictSeverity = Literal["high", "medium", "low"]
ConflictStatus = Literal["open", "resolved", "ignored"]


# ---------------------------------------------------------------------------
# 条件树节点
# ---------------------------------------------------------------------------
class ConditionLeaf(BaseModel):
    """条件叶子节点：对某个字段做一次比较判断。

    字段:
        field:    条件字段名（如 "attendance.late_count"、"order.amount"）。
        operator: 操作符，取值见 CONDITION_OPERATORS。
        value:    比较值；between 时为 [min, max]，in/not_in 时为列表，
                  empty/not_empty 时忽略。
    """
    model_config = ConfigDict(extra="forbid")

    type: Literal["condition"] = "condition"
    field: str
    operator: Operator
    value: Any = None

    @model_validator(mode="after")
    def _check_value_shape(self) -> "ConditionLeaf":
        # between 需要两个元素的列表，in/not_in 需要列表
        if self.operator == "between":
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError("operator='between' 时 value 必须为 [min, max] 两元素列表")
        if self.operator in ("in", "not_in"):
            if not isinstance(self.value, list):
                raise ValueError("operator='in'/'not_in' 时 value 必须为列表")
        return self


class LogicNode(BaseModel):
    """逻辑节点：以 and/or 组合子条件，构成条件树。

    字段:
        logic:    and 或 or。
        children: 子节点列表（可为叶子或嵌套逻辑节点）。
    """
    model_config = ConfigDict(extra="forbid")

    type: Literal["logic"] = "logic"
    logic: Literal["and", "or"] = "and"
    children: list["ConditionNode"]


# 条件树节点的判别联合类型（靠 type 字段区分 logic / condition）
ConditionNode = Annotated[
    Union[LogicNode, ConditionLeaf],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# 动作节点 / 生效范围
# ---------------------------------------------------------------------------
class Action(BaseModel):
    """动作节点：条件满足时执行的动作。

    字段:
        action_type: 动作类型，取值见 ACTION_TYPES。
        target:      动作目标（通知对象、目标字段、目标系统等）。
        channel:     渠道（email/sms/in_app/todo/system），可选。
        message:     文案模板，支持 {field} 占位符（如 {name}），可选。
        params:      其它结构化参数，默认空字典。
    """
    model_config = ConfigDict(extra="forbid")

    type: Literal["action"] = "action"
    action_type: ActionType
    target: str = ""
    channel: str | None = None
    message: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class Scope(BaseModel):
    """生效范围：规则作用于哪些对象，以及可选的额外范围过滤。

    字段:
        target_type: 作用对象类型（employee/customer/order/task/personal/other）。
        filters:     范围过滤条件列表，可为空。
    """
    model_config = ConfigDict(extra="forbid")

    target_type: str = "other"
    filters: list[ConditionNode] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 规则本体
# ---------------------------------------------------------------------------
class Rule(BaseModel):
    """规则：一次自然语言描述对应的结构化规则树。

    字段:
        rule_id:         规则唯一 ID（自动生成形如 R20261115_xxxxxxxx）。
        name:            规则名称。
        description:     自然语言描述/备注。
        version:         版本号，从 1 开始递增。
        priority:        优先级 0~100，数值越大越优先。
        status:          状态（enabled/disabled/draft）。
        category:        场景分类（人事/销售/财务/个人生活/其他）。
        effective_scope: 生效范围。
        conditions:      条件树（根节点）。
        actions:         动作节点列表。
        created_at:       创建时间（ISO8601 字符串）。
        updated_at:       更新时间（ISO8601 字符串）。
        author:           创建人/来源。
    """
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    name: str
    description: str = ""
    version: int = Field(default=1, ge=1)
    priority: int = Field(default=50, ge=0, le=100)
    status: RuleStatus = "enabled"
    category: str = "其他"
    effective_scope: Scope = Field(default_factory=Scope)
    conditions: ConditionNode
    actions: list[Action] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    author: str | None = None


# ---------------------------------------------------------------------------
# 冲突结构体（阶段1 使用，阶段0 冻结结构）
# ---------------------------------------------------------------------------
class Conflict(BaseModel):
    """规则冲突结构体：描述两条规则之间的冲突。

    字段:
        conflict_id:   冲突唯一 ID。
        rule_a_id:     规则 A 的 rule_id。
        rule_b_id:     规则 B 的 rule_id。
        conflict_type: 冲突类型（动作冲突/优先级冲突/条件重叠/互斥）。
        severity:      严重度（high/medium/low）。
        field:         涉及字段，可选。
        description:   冲突描述。
        status:        处理状态（open/resolved/ignored）。
        detected_at:   检测时间（ISO8601 字符串）。
    """
    model_config = ConfigDict(extra="forbid")

    conflict_id: str
    rule_a_id: str
    rule_b_id: str
    conflict_type: ConflictType
    severity: ConflictSeverity = "medium"
    field: str | None = None
    description: str = ""
    status: ConflictStatus = "open"
    detected_at: str | None = None


# ---------------------------------------------------------------------------
# LLM 输出信封（自然语言解析的返回结构）
# ---------------------------------------------------------------------------
class LLMResult(BaseModel):
    """LLM 翻译返回的信封：要么是可翻译的规则，要么是需要澄清的说明。

    字段:
        result_type: "rule" 表示成功翻译出规则；"clarification" 表示输入不完整/歧义，
                     无法构成规则，需向用户澄清。
        rule:        result_type=="rule" 时必填。
        message:     result_type=="clarification" 时填写的说明。
        questions:   需要向用户澄清的问题列表。
    """
    model_config = ConfigDict(extra="forbid")

    result_type: Literal["rule", "clarification"]
    rule: Rule | None = None
    message: str | None = None
    questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_envelope(self) -> "LLMResult":
        if self.result_type == "rule" and self.rule is None:
            raise ValueError("result_type='rule' 时 rule 字段不能为空")
        if self.result_type == "clarification" and not self.message:
            raise ValueError("result_type='clarification' 时 message 字段不能为空")
        return self


# ---------------------------------------------------------------------------
# 重建含前向引用的模型
# ---------------------------------------------------------------------------
LogicNode.model_rebuild()
Scope.model_rebuild()


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
# 项目统一时区：北京时间（UTC+8）
BEIJING_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    """获取当前北京时间（UTC+8）的时间字符串，精确到秒。

    出参:
        str: 形如 2026-11-15 22:43:53（yyyy-mm-dd hh:mm:ss，无时区后缀）。
    """
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


def gen_rule_id() -> str:
    """生成规则唯一 ID。

    出参:
        str: 形如 R20261115_a1b2c3d4。
    """
    date = datetime.now().strftime("%Y%m%d")
    return f"R{date}_{uuid.uuid4().hex[:8]}"


def gen_conflict_id() -> str:
    """生成冲突唯一 ID。

    出参:
        str: 形如 C20261115_a1b2c3d4。
    """
    date = datetime.now().strftime("%Y%m%d")
    return f"C{date}_{uuid.uuid4().hex[:8]}"
