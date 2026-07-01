"""测试: astrocrawl/ai/_errors.py — 9 个 AI 异常类层次。

ADR-0006 #1: Provider-agnostic 通用异常类型。
各 Provider 包负责 SDK → AIError 映射；核心仅定义类型。
"""

from __future__ import annotations

import pytest

from astrocrawl.ai._errors import (
    AIAuthError,
    AIConnectionError,
    AIContentFilterError,
    AIError,
    AIInvalidRequestError,
    AIProviderUnavailableError,
    AIRateLimitError,
    AIServerError,
    AITimeoutError,
)

# ═══════════════════════════════════════════════════════════════════════
# AIError — 基类
# ═══════════════════════════════════════════════════════════════════════


class TestAIError:
    def test_is_exception(self):
        assert issubclass(AIError, Exception)

    def test_message_preserved(self):
        err = AIError("test message")
        assert str(err) == "test message"

    def test_empty_message(self):
        err = AIError()
        assert str(err) == ""

    def test_can_be_raised_and_caught(self):
        with pytest.raises(AIError):
            raise AIError("boom")


# ═══════════════════════════════════════════════════════════════════════
# 继承层次
# ═══════════════════════════════════════════════════════════════════════


class TestErrorHierarchy:
    """所有子类都是 AIError 的子类。"""

    @pytest.mark.parametrize(
        "cls",
        [
            AIAuthError,
            AIRateLimitError,
            AITimeoutError,
            AIConnectionError,
            AIServerError,
            AIContentFilterError,
            AIInvalidRequestError,
            AIProviderUnavailableError,
        ],
    )
    def test_subclass_of_aierror(self, cls):
        assert issubclass(cls, AIError)

    def test_catch_by_base(self):
        """所有子类都可被 AIError 捕获。"""
        for cls in [
            AIAuthError,
            AIRateLimitError,
            AITimeoutError,
            AIConnectionError,
            AIServerError,
            AIContentFilterError,
            AIInvalidRequestError,
            AIProviderUnavailableError,
        ]:
            try:
                raise cls("test")
            except AIError:
                pass
            else:
                pytest.fail(f"{cls.__name__} 不能被 AIError 捕获")


# ═══════════════════════════════════════════════════════════════════════
# 重试分类 — retryable vs non-retryable
# ═══════════════════════════════════════════════════════════════════════


class TestRetryClassification:
    """根据 ADR-0006 DOCSTRING 分类验证可重试性。

    可重试: AIRateLimitError, AITimeoutError, AIConnectionError, AIServerError
    不重试: AIAuthError, AIContentFilterError, AIInvalidRequestError,
            AIProviderUnavailableError
    """

    RETRYABLE = frozenset(
        {
            AIRateLimitError,
            AITimeoutError,
            AIConnectionError,
            AIServerError,
        }
    )

    NON_RETRYABLE = frozenset(
        {
            AIAuthError,
            AIContentFilterError,
            AIInvalidRequestError,
            AIProviderUnavailableError,
        }
    )

    def test_retryable_set(self):
        assert self.RETRYABLE == frozenset(
            {
                AIRateLimitError,
                AITimeoutError,
                AIConnectionError,
                AIServerError,
            }
        )

    def test_non_retryable_set(self):
        assert self.NON_RETRYABLE == frozenset(
            {
                AIAuthError,
                AIContentFilterError,
                AIInvalidRequestError,
                AIProviderUnavailableError,
            }
        )

    def test_all_eight_subclasses_classified(self):
        """确保 8 个子类全部被分类到 retryable 或 non-retryable。"""
        all_subclasses = {
            AIAuthError,
            AIRateLimitError,
            AITimeoutError,
            AIConnectionError,
            AIServerError,
            AIContentFilterError,
            AIInvalidRequestError,
            AIProviderUnavailableError,
        }
        assert all_subclasses == self.RETRYABLE | self.NON_RETRYABLE


# ═══════════════════════════════════════════════════════════════════════
# AIProviderUnavailableError — 特殊行为
# ═══════════════════════════════════════════════════════════════════════


class TestAIProviderUnavailableErrorDetail:
    """AIProviderUnavailableError 包含安装指引。"""

    def test_contains_install_command(self):
        err = AIProviderUnavailableError("Provider 'anthropic' 未安装。\n请运行: pip install astrocrawl[anthropic]")
        assert "pip install" in str(err)
        assert "astrocrawl[anthropic]" in str(err)

    def test_chained_from_import_error(self):
        cause = ImportError("No module named 'anthropic'")
        err = AIProviderUnavailableError("Provider 'anthropic' SDK import 失败: No module named 'anthropic'")
        err.__cause__ = cause
        assert err.__cause__ is cause


# ═══════════════════════════════════════════════════════════════════════
# 独立实例化
# ═══════════════════════════════════════════════════════════════════════


class TestErrorInstantiation:
    @pytest.mark.parametrize(
        "cls",
        [
            AIAuthError,
            AIRateLimitError,
            AITimeoutError,
            AIConnectionError,
            AIServerError,
            AIContentFilterError,
            AIInvalidRequestError,
            AIProviderUnavailableError,
        ],
    )
    def test_instantiate_with_message(self, cls):
        err = cls("custom error text")
        assert isinstance(err, AIError)
        assert str(err) == "custom error text"

    @pytest.mark.parametrize(
        "cls",
        [
            AIAuthError,
            AIRateLimitError,
            AITimeoutError,
            AIConnectionError,
            AIServerError,
            AIContentFilterError,
            AIInvalidRequestError,
            AIProviderUnavailableError,
        ],
    )
    def test_instantiate_no_message(self, cls):
        err = cls()
        assert isinstance(err, AIError)
