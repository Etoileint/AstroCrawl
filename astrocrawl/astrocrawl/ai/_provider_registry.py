"""Provider 注册表 — importlib.metadata entry point 发现 + 优雅降级。

Entry point group: astrocrawl.ai.providers
Entry point callable 签名: create_provider(config, **provider_kwargs) -> _ChatProvider
list_models 通过 module-level getattr 约定发现（ADR-0007 决策 3）。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Callable

from astrobasis import LogfmtLogger
from astrocrawl.ai._errors import AIProviderUnavailableError

if TYPE_CHECKING:
    from astrocrawl.ai._config import AIConfig
    from astrocrawl.ai._provider import _ChatProvider

logger = LogfmtLogger("astrocrawl.ai")


def _get_entry_points(group: str):
    """Return importlib entry points for *group*.

    Requires Python 3.12+ for ``entry_points(group=...)`` keyword API.
    """
    from importlib.metadata import entry_points

    return entry_points(group=group)


def _discover_provider(
    provider_name: str,
    config: AIConfig,
    **provider_kwargs: Any,
) -> _ChatProvider:
    """通过 entry point 发现并加载 Provider。

    Args:
        provider_name: Provider 名（如 "openai", "anthropic", "google"）
        config: AIConfig 实例
        **provider_kwargs: 透传给 Provider __init__ 的专属参数

    Returns:
        _ChatProvider 实例

    Raises:
        AIProviderUnavailableError: Provider 未安装或 SDK import 失败
    """
    eps = _get_entry_points("astrocrawl.ai.providers")

    for ep in eps:
        if ep.name == provider_name:
            try:
                create_provider = ep.load()
            except Exception as e:
                raise AIProviderUnavailableError(
                    f"Provider '{provider_name}' SDK import 失败: {e}\n请运行: pip install astrocrawl[{provider_name}]"
                ) from e
            return create_provider(config, **provider_kwargs)  # type: ignore[no-any-return]

    raise AIProviderUnavailableError(
        f"Provider '{provider_name}' 未安装。\n请运行: pip install astrocrawl[{provider_name}]"
    )


def list_installed_providers() -> list[str]:
    """返回所有已安装 Provider 的名称列表。

    用于 GUI 下拉框动态填充（已安装亮显，未安装灰显）。
    """
    eps = _get_entry_points("astrocrawl.ai.providers")
    return sorted(ep.name for ep in eps)


def get_list_models_func(provider_name: str) -> Callable | None:
    """返回 provider 模块的 ``list_models`` 函数，不存在则返回 None。

    通过 convention-over-registration 发现：按 ``ep.module`` 导入模块，
    用 ``getattr(module, "list_models", None)`` 查找。

    ``list_models(base_url, api_key, timeout) -> list[str]``
    失败抛异常，调用方负责捕获。
    """
    eps = _get_entry_points("astrocrawl.ai.providers")
    for ep in eps:
        if ep.name == provider_name:
            try:
                ep.load()
                module = sys.modules[ep.module]
            except ImportError:
                return None
            return getattr(module, "list_models", None)
    return None
