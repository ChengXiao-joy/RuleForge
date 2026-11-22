"""Claude API 客户端封装。

职责：
- 构造 Anthropic 客户端并统一超时配置；
- 调用 messages.create 获取纯文本回复；
- 统一异常捕获（鉴权/限流/超时/网络/服务端错误），转译为领域异常 LLMError。

不负责：JSON 解析、Schema 校验、格式重试（由 llm_layer/parser.py 负责）。
"""
from __future__ import annotations

import anthropic

from utils import config
from utils.logger import get_logger

log = get_logger("llm.client")


class LLMError(Exception):
    """LLM 调用失败（网络/鉴权/限流/超时/服务端错误等）统一异常。"""


class ClaudeClient:
    """Claude API 客户端。

    入参:
        api_key: API 密钥；为空时读取 config.ANTHROPIC_API_KEY。
        model:   模型名；为空时读取 config.ANTHROPIC_MODEL。
        timeout: 单次请求超时（秒）；为空时读取 config.LLM_TIMEOUT。
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        key = api_key or config.ANTHROPIC_API_KEY
        if not key:
            raise LLMError(
                "未配置 ANTHROPIC_API_KEY，无法调用 Claude API。"
                "请在项目根目录 .env 中配置，或传入 api_key 参数。"
            )
        self.model = model or config.ANTHROPIC_MODEL
        # 连接超时固定 10s（网络不通时快速失败），读写超时用配置值；SDK 网络重试次数可配置。
        # 若配置了 ANTHROPIC_BASE_URL（如中转端点），则走自定义端点而非官方地址。
        kwargs: dict = {
            "api_key": key,
            "timeout": anthropic.Timeout(timeout or config.LLM_TIMEOUT, connect=10.0),
            "max_retries": config.LLM_SDK_MAX_RETRIES,
        }
        if config.ANTHROPIC_BASE_URL:
            kwargs["base_url"] = config.ANTHROPIC_BASE_URL
            log.info("使用自定义 API 端点：%s", config.ANTHROPIC_BASE_URL)
        self._client = anthropic.Anthropic(**kwargs)

    def complete(
        self,
        system: str,
        user_message: str,
        max_tokens: int | None = None,
    ) -> str:
        """调用 Claude 并返回纯文本回复。

        入参:
            system:       系统提示词。
            user_message: 用户消息。
            max_tokens:   输出 token 上限；为空时读取 config.LLM_MAX_TOKENS。

        出参:
            str: 模型输出的纯文本。

        异常:
            LLMError: 任何调用失败（鉴权/限流/超时/网络/服务端错误/拒绝）。
        """
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens or config.LLM_MAX_TOKENS,
                system=system,
                # 思考深度控制（low/medium/high/xhigh/max）
                output_config={"effort": config.LLM_EFFORT},
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.AuthenticationError as e:
            log.error("API Key 鉴权失败")
            raise LLMError(f"API Key 鉴权失败：{e}") from e
        except anthropic.RateLimitError as e:
            log.error("请求被限流")
            raise LLMError(f"请求被限流，请稍后重试：{e}") from e
        except anthropic.APITimeoutError as e:
            log.error("请求超时")
            raise LLMError(f"请求超时：{e}。请检查网络/代理状态后重试") from e
        except anthropic.APIConnectionError as e:
            log.error("网络连接失败")
            raise LLMError(f"网络连接失败：{e}。请检查网络/VPN 连接后重试") from e
        except anthropic.APIStatusError as e:
            log.error("API 返回错误状态 %s", e.status_code)
            if e.status_code == 403:
                # 403 forbidden：多为请求内容触发安全/组织策略，或账号无权访问该模型
                hint = (
                    "。403 通常是请求内容触发了安全策略或组织限制，"
                    "建议换一种说法再试；如仍失败请检查 API Key 是否有该模型的访问权限"
                )
            else:
                hint = ""
            raise LLMError(f"API 返回错误状态 {e.status_code}：{e}{hint}") from e
        except Exception as e:  # 兜底：防止 SDK 未知异常导致进程崩溃
            log.exception("调用 Claude 发生未知异常")
            raise LLMError(f"调用 Claude 失败：{e}") from e

        if response.stop_reason == "refusal":
            raise LLMError("Claude 拒绝了本次请求（安全策略）。")

        # 提取文本块拼接为完整输出
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        log.debug("Claude 返回 %d 字符", len(text))
        return text
