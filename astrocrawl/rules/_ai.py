"""AI 规则生成器 — 使用 AIClient 发送结构化 messages (system + user) 生成提取规则。

H5: 五层提示注入防御（OWASP LLM01）。输出验证 (#158) 是唯一不可绕过防线。
统一组装流水线：_assemble_messages 是所有路径的消息组装 SSOT。
"""

from __future__ import annotations

import json
import unicodedata
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup
from pydantic import ValidationError

from astrocrawl.ai import AIClient, GenerationParams, OutputConstraint
from astrocrawl.ai._types import ChatMessage, ChatResponse, Role
from astrocrawl.rules._chatml import count_tokens, serialize_chatml
from astrocrawl.rules._extractor import _extract_all_fields
from astrocrawl.rules._html_preprocess import PreprocessTier, preprocess_html
from astrocrawl.rules._markdown import clean_markdown_wrapper
from astrocrawl.rules._schema import RuleSchema, validate_rule
from astrocrawl.rules._template import get_prompt_template
from astrocrawl.utils.logging import LogfmtLogger

if TYPE_CHECKING:
    import threading

logger = LogfmtLogger("astrocrawl.rules.ai")


class GenerationCancelled(Exception):
    """AI 规则生成被用户取消。"""


_HTML_MAX_CHARS = 200000
_FIELD_MAX_LENGTH = 500

# Unicode TR39 可打印文本白名单 — 仅保留自然语言可见字符类别
_PRINTABLE_CAT_START = frozenset({"L", "N", "P", "S", "M"})


def _sanitize_printable(text: str) -> str:
    """移除 Unicode 非打印字符类别 (Cc/Cf/Cs/Co/Cn)。

    对标 Unicode TR39 §3.1：仅保留 Letter/Number/Punctuation/Symbol/Mark
    类别 + 空格分隔符 (Zs) + TAB/LF。
    无需枚举危险码点——类别覆盖所有当前和未来的非打印字符。
    """
    cleaned: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat[0] in _PRINTABLE_CAT_START or cat == "Zs" or ch in ("\t", "\n"):
            cleaned.append(ch)
    return "".join(cleaned)


def _sanitize_field_requirement(text: str) -> str:
    """H5 L2: 四层字段需求清洗。

    1. Unicode TR39 类别白名单 — 移除 Cc/Cf/Cs/Co/Cn
    2. ChatML 分隔符移除 — 防消息边界注入
    3. XML CDATA 移除 — 防上下文混淆
    4. 空白规范化 — 合并连续空白
    """
    cleaned = _sanitize_printable(text)
    cleaned = cleaned.replace("<|im_start|>", " ")
    cleaned = cleaned.replace("<|im_end|>", " ")
    cleaned = cleaned.replace("<![CDATA[", " ")
    cleaned = cleaned.replace("]]>", " ")
    return " ".join(cleaned.split())


def _assemble_user_message(url: str, html: str, field_requirements: list[str]) -> str:
    """构建 user message 内容字符串。Path A + Path B + CLI 共用。

    H5 L1: URL urlparse→urlunparse 重建，丢弃控制字符
    H5 L2: _sanitize_field_requirement — S40 清洗 + XML 标记移除
    截断 HTML 至 _HTML_MAX_CHARS
    H5 L3: <html_source> XML 包裹
    """
    # H5 L1
    parsed = urlparse(url)
    safe_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))

    # H5 L2
    safe_fields: list[str] = []
    for f in field_requirements:
        if not isinstance(f, str):
            continue
        cleaned = _sanitize_field_requirement(f)
        if cleaned:
            safe_fields.append(cleaned[:_FIELD_MAX_LENGTH])
    fields_str = ", ".join(safe_fields) if safe_fields else "自动检测"

    truncated_html = html[:_HTML_MAX_CHARS] if len(html) > _HTML_MAX_CHARS else html

    # H5 L3
    return (
        f"目标 URL: {safe_url}\n"
        f"需提取字段: {fields_str}\n\n"
        f"<html_source>\n{truncated_html}\n</html_source>\n\n"
        f"请输出规则 JSON:"
    )


# ── 消息组装 SSOT ──────────────────────────────────────────────────────────


def _assemble_messages(
    url: str,
    html: str,
    field_requirements: list[str],
    tier: PreprocessTier = PreprocessTier.CANONICAL,
    mode: str = "type",
) -> list[ChatMessage]:
    """所有路径的消息组装唯一入口。CLI + GUI Path A + GUI Path B 均调用此函数。"""
    if not html or not html.strip():
        raise ValueError("HTML 不能为空")

    preprocessed = preprocess_html(html, tier)
    user_content = _assemble_user_message(url, preprocessed, field_requirements)
    return [
        ChatMessage(role=Role.SYSTEM, content=get_prompt_template(mode)),
        ChatMessage(role=Role.USER, content=user_content),
    ]


def get_assembled_prompt(
    url: str,
    html: str,
    field_requirements: list[str],
    tier: PreprocessTier = PreprocessTier.CANONICAL,
    model: str = "gpt-4o-mini",
    mode: str = "type",
) -> tuple:
    """构建完整 ChatML 文本 + Token 统计。Path A 复制 + Path B 预览用。"""
    messages = _assemble_messages(url, html, field_requirements, tier, mode)
    chatml_text = serialize_chatml(messages)
    token_count = count_tokens(chatml_text, model)
    return chatml_text, token_count


# ── RuleGenerator ───────────────────────────────────────────────────────────


class RuleGenerator:
    """AI 规则生成器 — 结构化 messages，通用 AIClient 底座。

    ADR-0008: 支持 OutputConstraint 结构化输出 + 重试循环 (max_retries=3)。

    Usage::

        from astrocrawl.ai import AIClient, AIConfig, OutputConstraint
        client = AIClient(AIConfig(api_key="...", default_model="gpt-4.5"))
        gen = RuleGenerator(client)
        rule = gen.generate_sync("https://example.com", "<html>...", ["title", "price"])
    """

    _MAX_RESPONSE_RETRIES = 3  # 对齐 Instructor / Vercel / LangChain

    def __init__(self, client: AIClient) -> None:
        self._client = client

    # ── sync (GUI QThread 调用) ───────────────────────────

    def generate_sync(
        self,
        url: str,
        html: str,
        field_requirements: list[str],
        params: GenerationParams | None = None,
        tier: PreprocessTier = PreprocessTier.CANONICAL,
        mode: str = "type",
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """同步生成规则 JSON。所有 API 调用的统一入口。"""
        messages = _assemble_messages(url, html, field_requirements, tier, mode)
        p = params or self._default_params()
        return self._generate_with_retry(list(messages), p, source_html=html, cancel_event=cancel_event)

    # ── async (后台任务调用) ───────────────────────────────

    async def generate(
        self,
        url: str,
        html: str,
        field_requirements: list[str],
        params: GenerationParams | None = None,
        tier: PreprocessTier = PreprocessTier.CANONICAL,
        mode: str = "type",
    ) -> dict[str, Any]:
        """异步生成规则 JSON。"""
        messages = _assemble_messages(url, html, field_requirements, tier, mode)
        p = params or self._default_params()
        return await self._agenerate_with_retry(list(messages), p, source_html=html)

    def _generate_from_messages(
        self,
        messages: list[ChatMessage],
        params: GenerationParams | None = None,
    ) -> dict[str, Any]:
        """从预组装 messages 生成规则 JSON。内部 escape hatch，不被用户路径调用。"""
        p = params or self._default_params()
        return self._generate_with_retry(list(messages), p)

    # ── retry loop ──────────────────────────────────────────

    def _generate_with_retry(
        self,
        messages: list[ChatMessage],
        params: GenerationParams,
        *,
        source_html: str = "",
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """同步重试循环：chat → parse → validate，失败追加错误反馈。"""
        for attempt in range(self._MAX_RESPONSE_RETRIES):
            if cancel_event is not None and cancel_event.is_set():
                raise GenerationCancelled()
            response = self._client.chat(messages, params=params)
            try:
                return self._parse_and_validate_response(response, source_html)
            except (json.JSONDecodeError, ValueError, ValidationError) as e:
                if attempt == self._MAX_RESPONSE_RETRIES - 1:
                    raise
                messages.append(_build_error_feedback(e))
        return {}  # pragma: no cover — retry loop always raises or returns

    async def _agenerate_with_retry(
        self, messages: list[ChatMessage], params: GenerationParams, *, source_html: str = ""
    ) -> dict[str, Any]:
        """异步重试循环。"""
        for attempt in range(self._MAX_RESPONSE_RETRIES):
            response = await self._client.achat(messages, params=params)
            try:
                return self._parse_and_validate_response(response, source_html)
            except (json.JSONDecodeError, ValueError, ValidationError) as e:
                if attempt == self._MAX_RESPONSE_RETRIES - 1:
                    raise
                messages.append(_build_error_feedback(e))
        return {}  # pragma: no cover — retry loop always raises or returns

    # ── internal ──────────────────────────────────────────

    @staticmethod
    def _default_params() -> GenerationParams:
        """ADR-0008: 默认请求 json_schema，由 AIClient._resolve_output_format() 自动降级。"""
        return GenerationParams(
            temperature=0.1,
            max_tokens=16384,
            output=OutputConstraint(format="json_schema", schema_model=RuleSchema),
        )

    @staticmethod
    def _parse_and_validate_response(response: ChatResponse, source_html: str = "") -> dict[str, Any]:
        """H5 L4 + C4 L2 + ADR-0008: 输出验证——AI 产出必须通过 validate_rule。

        Tool Use 路径（Anthropic json_schema）: arguments 已是 dict，跳过 json.loads。
        文本路径（OpenAI/Google）: clean_markdown → json.loads。
        若提供 source_html，用提取引擎在源 HTML 上跑一遍，空字段反馈给 AI 重试。
        """
        if response.tool_calls:
            data = response.tool_calls[0].arguments
        else:
            cleaned = clean_markdown_wrapper(response.content)
            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError:
                logger.warning("ai_response_parse_failed", content_preview=response.content[:200])
                raise
        rule = validate_rule(data)
        if source_html:
            _validate_extraction(rule, source_html)
        return data


def _validate_extraction(rule: RuleSchema, html: str) -> None:
    """复用提取引擎在源 HTML 上跑一遍，空字段反馈给 AI 重试。"""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        logger.warning("validation_parse_error")
        return

    extracted = _extract_all_fields(soup, rule.fields)
    empty: list[str] = []
    for field_name in rule.fields:
        value = extracted.get(field_name)
        if value is None:
            empty.append(f"  {field_name}: 提取结果 None（选择器未命中或提取异常）")
        elif isinstance(value, list) and len(value) == 0:
            empty.append(f"  {field_name}: 提取结果 []（选择器命中 0 个元素）")
        elif isinstance(value, str) and not value.strip():
            empty.append(f"  {field_name}: 提取结果为空字符串")
    if empty:
        raise ValueError("以下字段在源页面提取结果为空（请检查 DOM 层级后修正选择器）：\n" + "\n".join(empty))


def _build_error_feedback(error: Exception) -> ChatMessage:
    """ADR-0008: 构建错误反馈消息，Pydantic ValidationError 提供字段级精准反馈。"""
    if isinstance(error, ValidationError):
        detail = "\n".join(f"- {'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in error.errors())
    else:
        detail = str(error)
    return ChatMessage(
        role=Role.USER,
        content=f"上次输出的规则校验失败：\n{detail}\n\n请修正后重新输出。只输出 JSON，不要其他内容。",
    )
