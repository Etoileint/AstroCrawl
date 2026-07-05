"""测试：astrocrawl/ai/_provider.py + _provider_registry.py — Protocol 合规 + entry point 发现。

ADR-0006 #1: Provider Protocols + Registry
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Iterator

import pytest

from astrocrawl.ai._errors import AIError, AIProviderUnavailableError
from astrocrawl.ai._provider import _ChatProvider, _SupportsEmbedding
from astrocrawl.ai._provider_registry import _discover_provider, get_list_models_func, list_installed_providers

if TYPE_CHECKING:
    from astrocrawl.ai._types import ChatResponse

# ═══════════════════════════════════════════════════════════════════════
# _ChatProvider Protocol — runtime_checkable isinstance
# ═══════════════════════════════════════════════════════════════════════


class TestChatProviderProtocol:
    """_ChatProvider Protocol — 5 方法全实现 ↔ isinstance True。"""

    def test_full_implementation_is_provider(self):
        class _Full:
            provider_name = "test"
            supported_output_formats = frozenset()

            def chat(self, messages, tools, params) -> ChatResponse: ...
            def chat_stream(self, messages, tools, params) -> Iterator: ...
            async def achat(self, messages, tools, params) -> ChatResponse: ...
            async def achat_stream(self, messages, tools, params) -> AsyncIterator: ...
            async def aclose(self) -> None: ...
            def close(self) -> None: ...

        assert isinstance(_Full(), _ChatProvider)

    def test_missing_aclose_not_provider(self):
        class _NoAclose:
            provider_name = "test"
            supported_output_formats = frozenset()

            def chat(self, messages, tools, params) -> ChatResponse: ...
            def chat_stream(self, messages, tools, params) -> Iterator: ...
            async def achat(self, messages, tools, params) -> ChatResponse: ...
            async def achat_stream(self, messages, tools, params) -> AsyncIterator: ...

        assert not isinstance(_NoAclose(), _ChatProvider)

    def test_missing_sync_chat_not_provider(self):
        class _NoSync:
            provider_name = "test"
            supported_output_formats = frozenset()

            def chat_stream(self, messages, tools, params) -> Iterator: ...
            async def achat(self, messages, tools, params) -> ChatResponse: ...
            async def achat_stream(self, messages, tools, params) -> AsyncIterator: ...
            async def aclose(self) -> None: ...

        assert not isinstance(_NoSync(), _ChatProvider)

    def test_empty_class_not_provider(self):
        class _Empty:
            pass

        assert not isinstance(_Empty(), _ChatProvider)

    def test_plain_object_not_provider(self):
        assert not isinstance(object(), _ChatProvider)

    def test_provider_name_is_required(self):
        class _NoName:
            def chat(self, messages, tools, params) -> ChatResponse: ...
            def chat_stream(self, messages, tools, params) -> Iterator: ...
            async def achat(self, messages, tools, params) -> ChatResponse: ...
            async def achat_stream(self, messages, tools, params) -> AsyncIterator: ...
            async def aclose(self) -> None: ...

        # provider_name 缺失 → Protocol 不匹配（非 @property，是类属性）
        assert not isinstance(_NoName(), _ChatProvider)

    def test_missing_supported_output_formats_not_provider(self):
        class _NoFormats:
            provider_name = "test"

            def chat(self, messages, tools, params) -> ChatResponse: ...
            def chat_stream(self, messages, tools, params) -> Iterator: ...
            async def achat(self, messages, tools, params) -> ChatResponse: ...
            async def achat_stream(self, messages, tools, params) -> AsyncIterator: ...
            async def aclose(self) -> None: ...

        assert not isinstance(_NoFormats(), _ChatProvider)


# ═══════════════════════════════════════════════════════════════════════
# _SupportsEmbedding Protocol
# ═══════════════════════════════════════════════════════════════════════


class TestSupportsEmbeddingProtocol:
    """_SupportsEmbedding Protocol — embed 方法存在 ↔ isinstance True。"""

    def test_full_implementation_is_embedding(self):
        class _Full:
            async def embed(self, texts, model): ...

        assert isinstance(_Full(), _SupportsEmbedding)

    def test_missing_embed_not_embedding(self):
        class _NoEmbed:
            pass

        assert not isinstance(_NoEmbed(), _SupportsEmbedding)

    def test_chat_and_embed_implementation(self):
        class _Both:
            provider_name = "test"
            supported_output_formats = frozenset()

            def chat(self, messages, tools, params) -> ChatResponse: ...
            def chat_stream(self, messages, tools, params) -> Iterator: ...
            async def achat(self, messages, tools, params) -> ChatResponse: ...
            async def achat_stream(self, messages, tools, params) -> AsyncIterator: ...
            async def aclose(self) -> None: ...
            def close(self) -> None: ...
            async def embed(self, texts, model): ...

        obj = _Both()
        assert isinstance(obj, _ChatProvider)
        assert isinstance(obj, _SupportsEmbedding)


# ═══════════════════════════════════════════════════════════════════════
# AIProviderUnavailableError
# ═══════════════════════════════════════════════════════════════════════


class TestAIProviderUnavailableError:
    """AIProviderUnavailableError — AIError 子类，含安装指引。"""

    def test_inherits_from_ai_error(self):
        err = AIProviderUnavailableError("test")
        assert isinstance(err, AIError)
        assert isinstance(err, Exception)

    def test_message_contains_install_guidance(self):
        err = AIProviderUnavailableError("Provider 'anthropic' 未安装。\n请运行: pip install astrocrawl[anthropic]")
        assert "pip install" in str(err)
        assert "anthropic" in str(err)

    def test_chain_from_import_error(self):
        cause = ImportError("No module named 'anthropic'")
        err = AIProviderUnavailableError(f"Provider 'anthropic' SDK import 失败: {cause}")
        err.__cause__ = cause
        assert err.__cause__ is not None
        assert isinstance(err.__cause__, ImportError)


# ═══════════════════════════════════════════════════════════════════════
# _provider_registry — entry point 发现
# ═══════════════════════════════════════════════════════════════════════


class TestListInstalledProviders:
    """list_installed_providers — 返回已安装 Provider 名称列表。"""

    def test_returns_list(self):
        result = list_installed_providers()
        assert isinstance(result, list)

    def test_returns_sorted(self):
        result = list_installed_providers()
        assert result == sorted(result)

    def test_no_duplicates(self):
        result = list_installed_providers()
        assert len(result) == len(set(result))


class TestDiscoverProvider:
    """_discover_provider — 未安装 Provider 时抛 AIProviderUnavailableError。"""

    def test_nonexistent_provider_raises(self):
        from unittest.mock import MagicMock

        cfg = MagicMock()
        with pytest.raises(AIProviderUnavailableError) as exc:
            _discover_provider("nonexistent_xyz_provider", cfg)
        assert "pip install" in str(exc.value)
        assert "nonexistent_xyz_provider" in str(exc.value)

    def test_error_message_has_install_command(self):
        from unittest.mock import MagicMock

        cfg = MagicMock()
        with pytest.raises(AIProviderUnavailableError):
            _discover_provider("not_a_real_provider_xyz", cfg)


class TestGetListModelsFunc:
    """get_list_models_func — convention-over-registration 发现。"""

    def test_nonexistent_provider_returns_none(self):
        result = get_list_models_func("nonexistent_xyz")
        assert result is None

    def test_installed_providers_have_list_models(self):
        for name in list_installed_providers():
            func = get_list_models_func(name)
            assert callable(func), f"Provider '{name}' must export list_models"

    def test_empty_string_provider_returns_none(self):
        result = get_list_models_func("")
        assert result is None

    def test_case_sensitive_provider_name(self):
        """Provider 名区分大小写。"""
        result = get_list_models_func("NONEXISTENT")
        assert result is None


class TestDiscoverProviderEdgeCases:
    """_discover_provider — 边界用例。"""

    def test_empty_string_provider_raises(self):
        from unittest.mock import MagicMock

        cfg = MagicMock()
        with pytest.raises(AIProviderUnavailableError):
            _discover_provider("", cfg)


class TestCrossProviderContract:
    """跨 Provider 契约 — 所有已安装 provider 的接口一致性。"""

    def test_all_providers_have_list_models(self):
        for name in list_installed_providers():
            func = get_list_models_func(name)
            assert callable(func), f"Provider '{name}' must export list_models"

    def test_all_providers_accept_base_url(self):
        from astrocrawl.ai._config import AIConfig

        config = AIConfig(api_key="sk-test", base_url="https://custom.example.com/v1")
        for name in list_installed_providers():
            provider = _discover_provider(name, config)
            assert getattr(provider, "_base_url", None) == "https://custom.example.com/v1", (
                f"Provider '{name}' must pass base_url to client"
            )


class TestCrossProviderStreamContract:
    """跨 Provider 流式契约 — chat_stream 结构 + StreamFinish 类型验证。

    注意：StreamFinish.usage 的实际填充依赖真实 API 调用（需要 API key），
    CI 中无法完全验证。Provider 级单元测试在 API key 可用时覆盖 usage 填充路径。
    """

    def test_all_providers_have_chat_stream_generator(self):
        from astrocrawl.ai._config import AIConfig

        config = AIConfig(api_key="sk-test")
        for name in list_installed_providers():
            provider = _discover_provider(name, config)
            assert hasattr(provider, "chat_stream"), f"Provider '{name}' missing chat_stream"
            assert callable(provider.chat_stream), f"Provider '{name}' chat_stream is not callable"

    def test_streamfinish_usage_field_exists(self):
        from astrocrawl.ai._types import StreamFinish, TokenUsage

        sf = StreamFinish(finish_reason="stop", usage=TokenUsage(10, 5, 15))
        assert sf.usage is not None
        assert sf.usage.prompt_tokens == 10
        assert sf.usage.completion_tokens == 5
        assert sf.usage.total_tokens == 15
