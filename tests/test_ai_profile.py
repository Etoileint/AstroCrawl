"""AIProfile frozen dataclass 测试 — 全覆盖：构造/不可变/相等/repr/序列化/反序列化/类型强制/边界。

测试矩阵: 默认值 (7) + 完全重写 (1) + 部分重写 (1) + frozendict (1) + 相等 (1)
          + repr 掩码/边界 (3) + to_dict (2) + from_dict (4) + 可选字段 (2) + 布尔字段 (1)
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from astrocrawl.ai._profile import AIProfile


class TestAIProfile:
    """AIProfile frozen dataclass 单元测试 — 构造与默认值。"""

    def test_defaults(self):
        p = AIProfile()
        assert p.name == ""
        assert p.provider == "openai"
        assert p.model == "gpt-4o-mini"
        assert p.temperature == 0.1
        assert p.max_tokens == 2048
        assert p.api_key == ""
        assert p.endpoint == ""
        assert p.enabled is True
        assert p.last_test_status is None
        assert p.last_test_time is None

    def test_full_override(self):
        p = AIProfile(
            name="production",
            provider="anthropic",
            model="claude-opus-4-7",
            temperature=0.3,
            max_tokens=4096,
            api_key="sk-xxx",
            endpoint="https://custom.api.com/v1",
            enabled=False,
            last_test_status="ok",
            last_test_time="2026-06-15T10:00:00Z",
        )
        assert p.name == "production"
        assert p.provider == "anthropic"
        assert p.model == "claude-opus-4-7"
        assert p.temperature == 0.3
        assert p.max_tokens == 4096
        assert p.api_key == "sk-xxx"
        assert p.endpoint == "https://custom.api.com/v1"
        assert p.enabled is False
        assert p.last_test_status == "ok"
        assert p.last_test_time == "2026-06-15T10:00:00Z"

    def test_partial_override(self):
        p = AIProfile(model="claude-opus-4-7", temperature=0.0)
        assert p.model == "claude-opus-4-7"
        assert p.temperature == 0.0
        assert p.provider == "openai"
        assert p.name == ""
        assert p.max_tokens == 2048
        assert p.api_key == ""

    def test_frozen_immutability(self):
        p = AIProfile()
        with pytest.raises(FrozenInstanceError):
            p.name = "changed"  # type: ignore[misc]

    def test_equality(self):
        a = AIProfile(name="x")
        b = AIProfile(name="x")
        c = AIProfile(name="y")
        assert a == b
        assert a != c
        assert b != c

    def test_enabled_false_inequality(self):
        """enabled 不同则不等。"""
        a = AIProfile(name="x", enabled=True)
        b = AIProfile(name="x", enabled=False)
        assert a != b

    def test_name_empty_string(self):
        p = AIProfile(name="")
        assert p.name == ""

    def test_hashable(self):
        """frozen=True 自动生成 __hash__，可用作 set 元素和 dict key。"""
        a = AIProfile(name="x")
        b = AIProfile(name="x")
        assert hash(a) == hash(b)
        s = {a, AIProfile(name="y")}
        assert len(s) == 2
        d = {a: "value"}
        assert d[a] == "value"


class TestAIProfileRepr:
    """__repr__ API Key 掩码 — 安全关键。"""

    def test_masks_long_api_key(self):
        p = AIProfile(name="t", api_key="sk-abcdefghijklmnop")
        r = repr(p)
        assert "sk-abcde..." in r
        assert "sk-abcdefghijklmnop" not in r

    def test_boundary_8_chars_no_mask(self):
        p = AIProfile(name="t", api_key="12345678")
        r = repr(p)
        assert "12345678" in r
        assert "..." not in r

    def test_short_key_no_mask(self):
        p = AIProfile(name="t", api_key="short")
        r = repr(p)
        assert "short" in r
        assert "..." not in r

    def test_empty_key_no_mask(self):
        p = AIProfile(name="t", api_key="")
        r = repr(p)
        assert "..." not in r

    def test_contains_all_field_names(self):
        r = repr(AIProfile(name="t", last_test_status="ok", last_test_time="ts"))
        for field in (
            "name",
            "provider",
            "model",
            "temperature",
            "max_tokens",
            "api_key",
            "endpoint",
            "enabled",
            "last_test_status",
            "last_test_time",
        ):
            assert field in r, f"repr missing field: {field}"


class TestAIProfileToDict:
    """to_dict() 序列化。"""

    def test_returns_all_10_keys(self):
        d = AIProfile().to_dict()
        expected = {
            "name",
            "provider",
            "model",
            "temperature",
            "max_tokens",
            "api_key",
            "endpoint",
            "enabled",
            "last_test_status",
            "last_test_time",
        }
        assert set(d.keys()) == expected

    def test_preserves_types(self):
        p = AIProfile(
            temperature=0.5,
            max_tokens=4096,
            enabled=False,
            last_test_status="ok",
            last_test_time="2026-01-01T00:00:00Z",
        )
        d = p.to_dict()
        assert isinstance(d["temperature"], float)
        assert isinstance(d["max_tokens"], int)
        assert isinstance(d["enabled"], bool)
        assert d["last_test_status"] == "ok"
        assert d["last_test_time"] == "2026-01-01T00:00:00Z"


class TestAIProfileFromDict:
    """from_dict() 反序列化 — 往返/默认/边界/强制。"""

    def test_full_roundtrip(self):
        p = AIProfile(
            name="full",
            provider="anthropic",
            model="claude-opus-4-7",
            temperature=0.5,
            max_tokens=8192,
            api_key="sk-xxx",
            endpoint="https://api.example.com",
            enabled=False,
            last_test_status="failed",
            last_test_time="2026-06-15T10:00:00Z",
        )
        assert AIProfile.from_dict(p.to_dict()) == p

    def test_empty_dict_equals_default(self):
        assert AIProfile.from_dict({}) == AIProfile()

    def test_missing_keys_use_defaults(self):
        p = AIProfile.from_dict({"name": "partial"})
        assert p.provider == "openai"
        assert p.model == "gpt-4o-mini"
        assert p.temperature == 0.1
        assert p.max_tokens == 2048
        assert p.api_key == ""
        assert p.endpoint == ""
        assert p.enabled is True
        assert p.last_test_status is None
        assert p.last_test_time is None

    def test_coerces_string_types(self):
        p = AIProfile.from_dict({"name": "c", "temperature": "0.5", "max_tokens": "8192", "enabled": ""})
        assert p.temperature == 0.5
        assert isinstance(p.temperature, float)
        assert p.max_tokens == 8192
        assert isinstance(p.max_tokens, int)
        assert p.enabled is False  # bool("") = False


class TestAIProfileOptionalFields:
    """last_test_status / last_test_time 可选字段。"""

    def test_last_test_status_default_none(self):
        assert AIProfile().last_test_status is None

    def test_last_test_status_ok(self):
        p = AIProfile(last_test_status="ok")
        assert p.last_test_status == "ok"

    def test_last_test_status_failed(self):
        p = AIProfile(last_test_status="failed")
        assert p.last_test_status == "failed"

    def test_last_test_time_default_none(self):
        assert AIProfile().last_test_time is None

    def test_last_test_time_set(self):
        p = AIProfile(last_test_time="2026-06-15T10:00:00Z")
        assert p.last_test_time == "2026-06-15T10:00:00Z"

    def test_from_dict_optional_none(self):
        p = AIProfile.from_dict({"name": "n", "last_test_status": None, "last_test_time": None})
        assert p.last_test_status is None
        assert p.last_test_time is None
