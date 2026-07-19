"""ChatML 序列化 + Token 统计测试。"""

from __future__ import annotations

from unittest import mock

from astrocrawl.ai._types import ChatMessage, Role
from astrocrawl.rules._chatml import count_tokens, serialize_chatml


class TestSerializeChatML:
    def test_messages_to_chatml(self):
        msgs = [
            ChatMessage(Role.SYSTEM, "You are a helper."),
            ChatMessage(Role.USER, "Hello!"),
        ]
        result = serialize_chatml(msgs)
        assert "<|im_start|>system" in result
        assert "You are a helper." in result
        assert "<|im_end|>" in result
        assert "<|im_start|>user" in result
        assert "Hello!" in result

    def test_empty_messages(self):
        result = serialize_chatml([])
        assert result == ""

    def test_single_message(self):
        msgs = [ChatMessage(Role.USER, "Hi")]
        result = serialize_chatml(msgs)
        assert "<|im_start|>user" in result
        assert "Hi<|im_end|>" in result

    def test_assistant_role(self):
        msgs = [ChatMessage(Role.ASSISTANT, "Response")]
        result = serialize_chatml(msgs)
        assert "<|im_start|>assistant" in result
        assert "Response<|im_end|>" in result

    def test_multiline_content(self):
        msgs = [ChatMessage(Role.USER, "Line 1\nLine 2")]
        result = serialize_chatml(msgs)
        assert "Line 1\nLine 2" in result

    def test_message_with_name(self):
        msgs = [ChatMessage(Role.SYSTEM, "You are helpful.", name="instructions")]
        result = serialize_chatml(msgs)
        assert "<|im_start|>system name=instructions" in result
        assert "You are helpful.<|im_end|>" in result

    def test_tool_message_with_tool_call_id(self):
        msgs = [ChatMessage(Role.TOOL, '{"result": 42}', tool_call_id="call_abc")]
        result = serialize_chatml(msgs)
        assert "<|im_start|>tool to=call_abc" in result
        assert '{"result": 42}<|im_end|>' in result

    def test_tool_call_id_takes_priority_over_name(self):
        msgs = [ChatMessage(Role.TOOL, "result", tool_call_id="call_xyz", name="ignored")]
        result = serialize_chatml(msgs)
        assert "to=call_xyz" in result
        assert "name=" not in result


class TestCountTokens:
    def test_returns_positive_for_text(self):
        count = count_tokens("Hello, world!")
        assert count > 0

    def test_returns_zero_for_empty(self):
        count = count_tokens("")
        assert count == 0

    def test_fallback_on_unknown_model(self):
        count = count_tokens("test", model="unknown-model-xyz")
        assert count > 0

    def test_long_text_returns_higher_count(self):
        short = count_tokens("hi")
        long = count_tokens("hello world this is a longer test sentence")
        assert long > short

    def test_chinese_text(self):
        count = count_tokens("你好世界")
        assert count > 0

    def test_tiktoken_not_installed_fallback(self):
        with mock.patch.dict("sys.modules", {"tiktoken": None}):
            count = count_tokens("Hello, world!")
        assert count == 0

    def test_encoding_exception_fallback(self):
        with mock.patch("tiktoken.encoding_for_model") as mock_enc:
            mock_enc.return_value.encode.side_effect = RuntimeError("boom")
            count = count_tokens("test")
        assert count == 0
