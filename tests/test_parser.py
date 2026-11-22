"""解析器单元测试（离线）。

用 FakeClient 模拟 Claude 输出，验证 JSON 提取、Schema 校验、自动重试逻辑，
不调用真实 API，可在无 Key 环境运行。
"""
from llm_layer import parser
from llm_layer.claude_client import LLMError
from llm_layer.parser import extract_json, parse_natural_language


class FakeClient:
    """伪造客户端：按队列顺序返回预设输出。"""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[str] = []

    def complete(self, system: str, user_message: str, max_tokens=None) -> str:
        self.calls.append(user_message)
        if not self.responses:
            raise LLMError("无更多预设响应")
        return self.responses.pop(0)


_VALID_RULE_JSON = (
    '{"result_type":"rule","rule":{"rule_id":"R1","name":"迟到提醒","version":1,'
    '"priority":50,"status":"enabled","category":"人事",'
    '"effective_scope":{"target_type":"employee","filters":[]},'
    '"conditions":{"type":"condition","field":"a","operator":"gt","value":3},'
    '"actions":[{"type":"action","action_type":"notify","target":"人事"}]}}'
)
_CLARIFY_JSON = '{"result_type":"clarification","message":"输入不完整","questions":["条件是什么？"]}'


# ---- extract_json ----
def test_extract_json_direct():
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_json_fence():
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_extract_json_surrounded():
    assert extract_json('这是解释 {"a": 1} 结束') == '{"a": 1}'


def test_extract_json_none():
    assert extract_json("没有 json") is None


# ---- parse_natural_language ----
def test_parse_success_first_try():
    r = parse_natural_language("迟到", client=FakeClient([_VALID_RULE_JSON]))
    assert r.success and r.rule is not None
    assert r.attempts == 1 and r.first_attempt_valid


def test_parse_clarification():
    r = parse_natural_language("处理一下", client=FakeClient([_CLARIFY_JSON]))
    assert r.success and r.needs_clarification
    assert r.rule is None


def test_parse_retry_on_bad_json_then_success():
    r = parse_natural_language("迟到", client=FakeClient(["不是json", _VALID_RULE_JSON]))
    assert r.success and r.attempts == 2


def test_parse_retry_on_schema_invalid_then_success():
    bad = '{"result_type":"rule"}'  # 缺 rule 字段，Schema 校验失败
    r = parse_natural_language("迟到", client=FakeClient([bad, _VALID_RULE_JSON]))
    assert r.success and r.attempts == 2


def test_parse_exhaust_retries():
    r = parse_natural_language("迟到", client=FakeClient(["a", "b", "c"]))
    assert not r.success and r.error


def test_parse_empty_input():
    r = parse_natural_language("   ")
    assert not r.success


def test_parse_llm_error():
    class Boom:
        def complete(self, system, user_message, max_tokens=None):
            raise LLMError("boom")

    r = parse_natural_language("迟到", client=Boom())
    assert not r.success and "boom" in r.error


def test_parser_module_exposed():
    assert hasattr(parser, "parse_natural_language")
    assert hasattr(parser, "extract_json")
