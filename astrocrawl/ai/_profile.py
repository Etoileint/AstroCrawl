"""AI 配置 Profile — 可持久化的单组 AI 配置（ADR-0007: 十字段）。

对标 AIConfig/CrawlerConfig frozen 模式。零内部导入（纯数据）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class AIProfile:
    name: str = ""
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    temperature: float = 0.1
    max_tokens: int = 16384
    api_key: str = ""
    endpoint: str = ""
    enabled: bool = True
    last_test_status: Literal["ok", "failed"] | None = None
    last_test_time: str | None = None

    def __repr__(self) -> str:
        key = f"{self.api_key[:8]}..." if len(self.api_key) > 8 else self.api_key
        return (
            f"AIProfile(name={self.name!r}, provider={self.provider!r}, model={self.model!r}, "
            f"api_key={key!r}, endpoint={self.endpoint!r}, temperature={self.temperature}, "
            f"max_tokens={self.max_tokens}, enabled={self.enabled}, "
            f"last_test_status={self.last_test_status!r}, last_test_time={self.last_test_time!r})"
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "api_key": self.api_key,
            "endpoint": self.endpoint,
            "enabled": self.enabled,
            "last_test_status": self.last_test_status,
            "last_test_time": self.last_test_time,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AIProfile:
        return cls(
            name=d.get("name", ""),
            provider=d.get("provider", "openai"),
            model=d.get("model", "gpt-4o-mini"),
            temperature=float(d.get("temperature", 0.1)),
            max_tokens=int(d.get("max_tokens", 16384)),
            api_key=d.get("api_key", ""),
            endpoint=d.get("endpoint", ""),
            enabled=bool(d.get("enabled", True)),
            last_test_status=d.get("last_test_status"),
            last_test_time=d.get("last_test_time"),
        )
