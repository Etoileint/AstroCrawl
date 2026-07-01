"""Markdown 清洗 — 剥离 markdown 代码块和 JSON 前后的非 JSON 文本 (N42)。

纯字符串工具，无文件 I/O 依赖。被 _ai.py（AI 响应解析）和 _io.py（导入预览）共同使用。
"""

from __future__ import annotations

from typing import cast

# CommonMark §4.5: closing ``` does not require preceding newline
_MARKDOWN_FENCE_PATTERN = r"```(?:json)?[\s]*\n([\s\S]*?)```"


def clean_markdown_wrapper(text: str) -> str:
    """剥离 markdown 代码块，返回 JSON 字符串。

    所有正则通过 re2 执行（线性时间，ReDoS 免疫）。re2 为硬依赖 (ADR-0005 D12)。
    """
    import re2

    m = re2.search(_MARKDOWN_FENCE_PATTERN, text)
    if m:
        return cast("str", m.group(1).strip())

    return text
