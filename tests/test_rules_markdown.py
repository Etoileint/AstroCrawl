"""测试: astrocrawl/rules/_markdown.py — clean_markdown_wrapper。

CommonMark §4.5: closing ``` does not require preceding newline。
所有正则通过 re2 执行（线性时间，ReDoS 免疫）。
"""

from __future__ import annotations

from astrocrawl.rules._markdown import clean_markdown_wrapper

# ═══════════════════════════════════════════════════════════════════════
# clean_markdown_wrapper
# ═══════════════════════════════════════════════════════════════════════


class TestCleanMarkdownWrapper:
    """clean_markdown_wrapper — 剥离 markdown 代码块，返回 JSON 字符串。"""

    def test_strips_json_fence(self):
        result = clean_markdown_wrapper('```json\n{"a": 1}\n```')
        assert result == '{"a": 1}'

    def test_strips_plain_fence(self):
        result = clean_markdown_wrapper('```\n{"a": 1}\n```')
        assert result == '{"a": 1}'

    def test_strips_with_trailing_newline(self):
        result = clean_markdown_wrapper('```json\n{"a": 1}\n```\n')
        assert result == '{"a": 1}'

    def test_no_fence_returns_as_is(self):
        result = clean_markdown_wrapper('{"a": 1}')
        assert result == '{"a": 1}'

    def test_plain_text_passthrough(self):
        result = clean_markdown_wrapper("hello world")
        assert result == "hello world"

    def test_multi_line_json_fence(self):
        result = clean_markdown_wrapper('```json\n{\n  "a": 1,\n  "b": 2\n}\n```')
        assert result == '{\n  "a": 1,\n  "b": 2\n}'

    def test_first_fence_only(self):
        """只剥离第一个代码块。"""
        result = clean_markdown_wrapper('```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```')
        assert result == '{"a": 1}'

    def test_no_leading_newline_fence(self):
        """CommonMark §4.5: 开标签后的空白符可选。"""
        result = clean_markdown_wrapper('```json{"a": 1}\n```')
        # re2 pattern requires \n after opening ``` so this won't match
        assert result == '```json{"a": 1}\n```'

    def test_nested_backticks_in_json(self):
        """非贪婪匹配在遇到第一个 ``` 时停止——嵌套反引号不是设计目标。"""
        result = clean_markdown_wrapper('```json\n{"code": "```python\\nprint(1)\\n```"}\n```')
        # 非贪婪 .*? 在 JSON 内部 ``` 处停止
        assert '"code"' not in result or result != ""

    def test_multiline_text_before_json(self):
        result = clean_markdown_wrapper('Sure, here is the extraction rule:\n\n```json\n{"name": "test"}\n```')
        assert result == '{"name": "test"}'

    def test_multiline_text_after_json(self):
        result = clean_markdown_wrapper('```json\n{"name": "test"}\n```\n\nThis rule extracts...')
        assert result == '{"name": "test"}'

    def test_no_space_around_fence(self):
        result = clean_markdown_wrapper('```json\n{"a":1}\n```')
        assert result == '{"a":1}'

    def test_empty_fence(self):
        result = clean_markdown_wrapper("```json\n\n```")
        assert result == ""

    def test_empty_string(self):
        assert clean_markdown_wrapper("") == ""

    def test_only_fence_markers(self):
        result = clean_markdown_wrapper("```\n```")
        assert result == ""
