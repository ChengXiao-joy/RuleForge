# Rule Schema 定稿（阶段0 冻结）

> 版本：v1.0（2026-11-15 冻结）
> 权威实现：`rule_engine/schema.py`（pydantic 模型，所有字段 `extra="forbid"`）
>
> **冻结声明**：本 Schema 是规则结构化输出的唯一权威定义。后续开发【禁止】私自新增、修改字段。
> 如确需演进，必须先修订本文档 + `schema.py`，并全量回归测试用例，经评审后再升版本。

---

## 1. 设计原则

1. **单一权威**：所有层（LLM 翻译、存储、引擎）以 `schema.py` 为准。
2. **判别联合**：条件树节点靠 `type` 字段区分「逻辑节点」与「条件叶子」，可任意嵌套。
3. **防漂移**：pydantic `extra="forbid"` 确保 LLM 或调用方无法塞入未定义字段；枚举用 `Literal` 冻结。
4. **轻量**：纯 pydantic 模型，可 `model_dump()`/`model_dump_json()` 序列化，无外部服务。

---

## 2. 规则（Rule）顶层结构

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `rule_id` | string | 是 | — | 规则唯一 ID，形如 `R20261115_a1b2c3d4` |
| `name` | string | 是 | — | 规则名称 |
| `description` | string | 否 | `""` | 自然语言描述/备注 |
| `version` | int | 否 | `1` | 版本号，从 1 递增（`>=1`） |
| `priority` | int | 否 | `50` | 优先级 0~100，越大越优先 |
| `status` | string | 否 | `enabled` | `enabled` / `disabled` / `draft` |
| `category` | string | 否 | `其他` | 场景分类：人事/销售/财务/个人生活/其他 |
| `effective_scope` | object(Scope) | 否 | 见下 | 生效范围 |
| `conditions` | object(ConditionNode) | 是 | — | 条件树根节点 |
| `actions` | array(Action) | 否 | `[]` | 动作节点列表 |
| `created_at` | string | 否 | `null` | 创建时间，统一北京时间，格式 `yyyy-mm-dd hh:mm:ss`（如 2026-11-15 22:15:20） |
| `updated_at` | string | 否 | `null` | 更新时间，统一北京时间，格式 `yyyy-mm-dd hh:mm:ss` |
| `author` | string | 否 | `null` | 创建人/来源 |

---

## 3. 生效范围（Scope）

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `target_type` | string | 否 | `other` | 作用对象：`employee`/`customer`/`order`/`task`/`personal`/`other` |
| `filters` | array(ConditionNode) | 否 | `[]` | 范围过滤条件列表，可为空 |

---

## 4. 条件树节点（ConditionNode）

条件树是「逻辑节点 + 条件叶子」的判别联合，靠 `type` 字段区分。

### 4.1 逻辑节点（LogicNode）

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `type` | string | 是 | `logic` | 固定值 |
| `logic` | string | 否 | `and` | `and` / `or` |
| `children` | array(ConditionNode) | 是 | — | 子节点列表，可嵌套 |

### 4.2 条件叶子（ConditionLeaf）

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `type` | string | 是 | `condition` | 固定值 |
| `field` | string | 是 | — | 条件字段名（如 `attendance.late_count`、`order.amount`） |
| `operator` | string | 是 | — | 操作符（见下表） |
| `value` | any | 否 | `null` | 比较值 |

### 4.3 操作符（冻结枚举）

| 操作符 | 含义 | value 要求 |
| --- | --- | --- |
| `eq` / `ne` | 等于 / 不等于 | 标量 |
| `gt` / `gte` | 大于 / 大于等于 | 数值 |
| `lt` / `lte` | 小于 / 小于等于 | 数值 |
| `contains` / `not_contains` | 包含 / 不包含 | 字符串 |
| `in` / `not_in` | 在列表中 / 不在列表中 | 列表 |
| `between` | 区间内 | `[min, max]` 两元素列表 |
| `empty` / `not_empty` | 为空 / 非空 | 可省略（填 null） |

---

## 5. 动作节点（Action）

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `type` | string | 是 | `action` | 固定值 |
| `action_type` | string | 是 | — | 动作类型（见下表） |
| `target` | string | 否 | `""` | 动作目标（对象/字段/系统） |
| `channel` | string | 否 | `null` | 渠道：`email`/`sms`/`in_app`/`todo`/`system` |
| `message` | string | 否 | `null` | 文案模板，支持 `{字段}` 占位符 |
| `params` | object | 否 | `{}` | 其它结构化参数 |

### 动作类型（冻结枚举）

| 动作类型 | 含义 |
| --- | --- |
| `notify` | 通知提醒 |
| `approve` | 审批 |
| `tag` | 打标签 |
| `set_field` | 设置字段/状态 |
| `create_task` | 创建任务 |
| `block` | 拦截/阻止 |

---

## 6. 冲突结构体（Conflict）—— 阶段1 使用，阶段0 冻结

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `conflict_id` | string | 是 | — | 冲突唯一 ID |
| `rule_a_id` | string | 是 | — | 规则 A 的 rule_id |
| `rule_b_id` | string | 是 | — | 规则 B 的 rule_id |
| `conflict_type` | string | 是 | — | `action_conflict`/`priority_conflict`/`condition_overlap`/`mutual_exclusion` |
| `severity` | string | 否 | `medium` | `high`/`medium`/`low` |
| `field` | string | 否 | `null` | 涉及字段 |
| `description` | string | 否 | `""` | 冲突描述 |
| `status` | string | 否 | `open` | `open`/`resolved`/`ignored` |
| `detected_at` | string | 否 | `null` | 检测时间 ISO8601 |

---

## 7. LLM 返回信封（LLMResult）

自然语言解析时，LLM 返回信封，二选一：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `result_type` | string | 是 | `rule`（成功翻译）/ `clarification`（需澄清） |
| `rule` | object(Rule) | rule 时必填 | 翻译出的规则 |
| `message` | string | clarification 时必填 | 需要澄清的说明 |
| `questions` | array(string) | 否 | 需向用户澄清的问题列表 |

---

## 8. 完整示例

```json
{
  "result_type": "rule",
  "rule": {
    "rule_id": "R20261115_a1b2c3d4",
    "name": "VIP大额订单折扣",
    "description": "订单金额超1000元且客户为VIP时批准8折",
    "version": 1,
    "priority": 60,
    "status": "enabled",
    "category": "销售",
    "effective_scope": { "target_type": "order", "filters": [] },
    "conditions": {
      "type": "logic",
      "logic": "and",
      "children": [
        { "type": "condition", "field": "order.amount", "operator": "gt", "value": 1000 },
        { "type": "condition", "field": "customer.level", "operator": "eq", "value": "VIP" }
      ]
    },
    "actions": [
      {
        "type": "action",
        "action_type": "approve",
        "target": "折扣",
        "channel": null,
        "message": "批准8折优惠",
        "params": { "discount": 0.8 }
      }
    ],
    "created_at": null,
    "updated_at": null,
    "author": null
  }
}
```
