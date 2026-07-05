"""Provider 接口 Protocol — _ChatProvider + _SupportsEmbedding。

ISP 分离：Chat 和 Embeddings 是不同的接口，不设统一 BaseProvider。
所有方法使用 PEP 563 惰性注解——TYPE_CHECKING 中导入的类型在运行时为字符串。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Iterator, Protocol, runtime_checkable

if TYPE_CHECKING:
    from astrocrawl.ai._config import _ResolvedParams
    from astrocrawl.ai._types import ChatMessage, ChatResponse, EmbedResult, StreamEvent


@runtime_checkable
class _ChatProvider(Protocol):
    """所有 Provider 必须实现。5 方法（3 async + 2 sync），零桩方法。

    sync + async 双套理由：三个 Provider 的官方 SDK 均原生提供双套 API。
    若只定义 async，AIClient 的 sync 方法只能通过 asyncio.run() 桥接——
    每次调用创建独立事件循环，导致 asyncio.Semaphore 限流器跨调用失效。
    """

    provider_name: str
    supported_output_formats: frozenset[
        str
    ]  # ADR-0008: Provider 支持的结构化输出格式集合，如 frozenset({"json_object", "json_schema"})

    # ── sync ──────────────────────────────────────────────

    def chat(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None,
        params: _ResolvedParams,
    ) -> ChatResponse: ...

    def chat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None,
        params: _ResolvedParams,
    ) -> Iterator[StreamEvent]:
        """同步流式对话。

        Contract:
        - 成功时必须 yield 恰好一个 StreamFinish 作为终端事件。
        - StreamFinish.usage 当 SDK 提供了 usage 数据时必须填充。
          仅当 SDK 确实不暴露 usage 时才为 None。
        - Anthropic: total_tokens = input_tokens + output_tokens 之和。
        """
        ...

    # ── async ─────────────────────────────────────────────

    async def achat(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None,
        params: _ResolvedParams,
    ) -> ChatResponse: ...

    def achat_stream(
        self,
        messages: list[ChatMessage],
        tools: list[dict] | None,
        params: _ResolvedParams,
    ) -> AsyncIterator[StreamEvent]:
        """异步流式对话。

        Contract: 与 chat_stream 相同——成功时必须 yield StreamFinish 作为终端事件，
        StreamFinish.usage 当 SDK 提供时必须填充。
        """
        ...

    async def aclose(self) -> None:
        """关闭底层客户端连接。幂等——多次调用安全，实现应检查客户端是否存在再关闭。"""
        ...

    def close(self) -> None:
        """关闭底层同步客户端。幂等。"""
        ...


@runtime_checkable
class _SupportsEmbedding(Protocol):
    """可选能力——仅 OpenAI / Google 实现。1 方法，async-only。"""

    async def embed(self, texts: list[str], model: str) -> EmbedResult: ...
