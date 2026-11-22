"""自然语言解析器。

职责：调用 LLM -> 提取 JSON -> 用 Rule Schema 校验 -> 格式错误自动重试（最多 config.JSON_MAX_RETRIES 次）。

红线：本模块只做「翻译 + 格式校验」，不做任何业务判断；冲突检测、仿真、推理等确定性逻辑
一律不在本层出现。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from llm_layer.claude_client import ClaudeClient, LLMError
from llm_layer import prompts
from rule_engine.schema import LLMResult, Rule
from utils import config
from utils.logger import get_logger

log = get_logger("llm.parser")


@dataclass
class ParseResult:
    """解析结果。

    字段:
        success:                是否成功（成功得到合法 JSON 响应，rule 或 clarification 均算成功）。
        rule:                   成功翻译出的规则（result_type=rule 时非空）。
        needs_clarification:    是否为「需要澄清」响应。
        clarification_message:  需要澄清时的说明。
        questions:              需要向用户澄清的问题列表。
        attempts:               成功时所用的尝试次数（1 表示一次通过）。
        error:                  最终失败原因。
        raw_outputs:            每次尝试的 LLM 原始输出（用于排查）。
    """

    success: bool
    rule: Rule | None = None
    needs_clarification: bool = False
    clarification_message: str | None = None
    questions: list[str] = field(default_factory=list)
    attempts: int = 0
    error: str | None = None
    raw_outputs: list[str] = field(default_factory=list)

    @property
    def first_attempt_valid(self) -> bool:
        """是否一次输出合法 JSON（验收指标）。"""
        return self.success and self.attempts == 1


def extract_json(text: str) -> str | None:
    """从 LLM 输出中提取 JSON 字符串。

    处理三种常见情况：直接 JSON、```json 围栏、JSON 前后夹杂其它文字。

    入参:
        text: LLM 原始输出。

    出参:
        str | None: 可被 json.loads 解析的 JSON 字符串；提取失败返回 None。
    """
    if not text:
        return None
    text = text.strip()

    # 1) 去掉 markdown 代码块围栏
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()

    # 2) 直接解析
    try:
        json.loads(text)
        return text
    except (json.JSONDecodeError, ValueError):
        pass

    # 3) 截取第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = text[start : end + 1]
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def parse_natural_language(
    natural_language: str,
    client: ClaudeClient | None = None,
) -> ParseResult:
    """将自然语言翻译为结构化规则（含格式校验与自动重试）。

    入参:
        natural_language: 用户输入的自然语言文本。
        client:           ClaudeClient 实例；为空时内部创建（需已配置 API Key）。

    出参:
        ParseResult: 解析结果，字段含义见 ParseResult 定义。
    """
    if not natural_language or not natural_language.strip():
        return ParseResult(success=False, error="输入为空，无法解析。")

    try:
        cli = client or ClaudeClient()
    except LLMError as e:
        return ParseResult(success=False, error=str(e))

    max_retries = max(0, config.JSON_MAX_RETRIES)  # 最多重试次数
    raw_outputs: list[str] = []
    last_error: str | None = None

    for attempt in range(1, max_retries + 2):  # 1 次初始 + N 次重试
        user_msg = (
            prompts.build_user_message(natural_language)
            if last_error is None
            else prompts.build_retry_user_message(natural_language, last_error)
        )

        try:
            raw = cli.complete(prompts.SYSTEM_PROMPT, user_msg)
        except LLMError as e:
            return ParseResult(
                success=False, error=f"LLM 调用失败：{e}", raw_outputs=raw_outputs
            )
        raw_outputs.append(raw)

        # 提取 JSON
        json_str = extract_json(raw)
        if json_str is None:
            last_error = "输出中未找到合法 JSON（可能是空输出、Markdown 围栏或夹杂其它文字）。"
            log.warning("第 %d 次尝试：未提取到 JSON", attempt)
            continue

        # 解析为 dict
        try:
            obj = json.loads(json_str)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = f"JSON 解析失败：{e}"
            log.warning("第 %d 次尝试：JSON 解析失败", attempt)
            continue

        # 用 Schema 校验（LLMResult 信封 + 内层 Rule）
        try:
            llm_result = LLMResult.model_validate(obj)
        except ValidationError as e:
            last_error = f"JSON 不符合 Rule Schema：{e}"
            log.warning("第 %d 次尝试：Schema 校验失败", attempt)
            continue

        # 校验通过
        if llm_result.result_type == "rule":
            return ParseResult(
                success=True,
                rule=llm_result.rule,
                attempts=attempt,
                raw_outputs=raw_outputs,
            )
        return ParseResult(
            success=True,
            needs_clarification=True,
            clarification_message=llm_result.message,
            questions=llm_result.questions,
            attempts=attempt,
            raw_outputs=raw_outputs,
        )

    # 重试耗尽仍未通过
    return ParseResult(
        success=False,
        error=f"重试 {max_retries} 次后仍无法得到合法 JSON：{last_error}",
        raw_outputs=raw_outputs,
    )
