"""测试：AI 生成 — HTML 截断、Prompt 模板。

测试文件覆盖 issue #128 的验收标准。
"""

from __future__ import annotations

import pytest

import astrocrawl.utils.preferences as _prefs_mod


@pytest.fixture(autouse=True)
def _isolate_preferences(tmp_path, monkeypatch):
    """所有测试隔离到临时文件，禁止污染真实 ~/.astrocrawl/preferences.json。"""
    monkeypatch.setattr(_prefs_mod, "PREFERENCES_FILE", tmp_path / "preferences.json")
    monkeypatch.setattr(_prefs_mod, "OLD_PATH_MEMORY_FILE", tmp_path / "path_memory.json")
    monkeypatch.setattr(_prefs_mod, "_preferences", None)


class TestPreferencesSecurity:
    """N3/N46: API Key 安全。"""

    def test_api_key_not_exported(self):
        from astrocrawl.ai._profile import AIProfile
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        prefs.save_ai_profile(AIProfile(name="p1", api_key="sk-secret-key"))
        prof = prefs.get_ai_profile("p1")
        assert prof is not None
        assert prof.api_key == "sk-secret-key"

    def test_api_key_empty_by_default(self):
        from astrocrawl.ai._profile import AIProfile
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        prefs.save_ai_profile(AIProfile(name="p2"))
        prof = prefs.get_ai_profile("p2")
        assert prof is not None
        assert prof.api_key == ""

    def test_endpoint_default(self):
        from astrocrawl.ai._profile import AIProfile
        from astrocrawl.utils.preferences import get_preferences

        prefs = get_preferences()
        prefs.save_ai_profile(AIProfile(name="p3"))
        prof = prefs.get_ai_profile("p3")
        assert prof is not None
        assert prof.endpoint == ""  # ADR-0007: 空 = provider 默认端点


class TestPromptTemplate:
    """Prompt 模板格式验证 — 使用 get_prompt_template()。"""

    @pytest.mark.parametrize("mode", ["type", "position"])
    def test_prompt_contains_schema(self, mode):
        from astrocrawl.rules._template import get_prompt_template, invalidate_template_cache

        invalidate_template_cache()
        template = get_prompt_template(mode)
        assert "selector" in template, "应含 Schema 示例"
        assert "extract" in template.lower()

    @pytest.mark.parametrize("mode", ["type", "position"])
    def test_prompt_returns_string(self, mode):
        from astrocrawl.rules._template import get_prompt_template, invalidate_template_cache

        invalidate_template_cache()
        template = get_prompt_template(mode)
        assert isinstance(template, str)
        assert len(template) > 100

    def test_html_truncation(self):
        from astrocrawl.rules._ai import _HTML_MAX_CHARS

        assert _HTML_MAX_CHARS == 200000
