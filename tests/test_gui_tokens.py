"""gui/_tokens.py 常量验证。

覆盖:
- TK01-TK11: 10 个布局常量 + PREVIEW_FIELD_COLORS 的存在性和值正确性
"""

from __future__ import annotations


class TestTokensExist:
    def test_space_xs(self):
        from astrocrawl.gui._tokens import SPACE_XS

        assert SPACE_XS == 4

    def test_space_sm(self):
        from astrocrawl.gui._tokens import SPACE_SM

        assert SPACE_SM == 6

    def test_space_md(self):
        from astrocrawl.gui._tokens import SPACE_MD

        assert SPACE_MD == 8

    def test_space_lg(self):
        from astrocrawl.gui._tokens import SPACE_LG

        assert SPACE_LG == 12

    def test_radius_sm(self):
        from astrocrawl.gui._tokens import RADIUS_SM

        assert RADIUS_SM == 3

    def test_radius_md(self):
        from astrocrawl.gui._tokens import RADIUS_MD

        assert RADIUS_MD == 4

    def test_font_sm(self):
        from astrocrawl.gui._tokens import FONT_SM

        assert FONT_SM == 11

    def test_font_md(self):
        from astrocrawl.gui._tokens import FONT_MD

        assert FONT_MD == 12

    def test_bar_height(self):
        from astrocrawl.gui._tokens import BAR_HEIGHT

        assert BAR_HEIGHT == 24

    def test_pulse_anim_ms(self):
        from astrocrawl.gui._tokens import PULSE_ANIM_MS

        assert PULSE_ANIM_MS == 33


class TestTokensImmutability:
    """Token 常量应为不可变原语——int 天然不可变，仅验证导出数量正确。"""

    def test_public_name_count(self):
        import astrocrawl.gui._tokens as t

        public = [n for n in dir(t) if not n.startswith("_") and n != "annotations"]
        assert len(public) == 11

    def test_all_tokens_are_ints(self):
        import astrocrawl.gui._tokens as t

        for name in dir(t):
            if name.startswith("_") or name == "annotations":
                continue
            if name == "PREVIEW_FIELD_COLORS":
                continue  # 10 色调色板 tuple，非 int 类型
            assert isinstance(getattr(t, name), int), f"{name} 应为 int"


class TestPreviewFieldColors:
    """PREVIEW_FIELD_COLORS — 10 色调色板 tuple，供规则预览覆盖层注入。"""

    def test_is_tuple(self):
        from astrocrawl.gui._tokens import PREVIEW_FIELD_COLORS

        assert isinstance(PREVIEW_FIELD_COLORS, tuple)

    def test_has_ten_colors(self):
        from astrocrawl.gui._tokens import PREVIEW_FIELD_COLORS

        assert len(PREVIEW_FIELD_COLORS) == 10

    def test_all_are_valid_hex(self):
        import re

        from astrocrawl.gui._tokens import PREVIEW_FIELD_COLORS

        hex_re = re.compile(r"^#[0-9A-Fa-f]{6}$")
        for color in PREVIEW_FIELD_COLORS:
            assert hex_re.match(color), f"{color} 不是有效 hex 颜色"

    def test_no_duplicate_colors(self):
        from astrocrawl.gui._tokens import PREVIEW_FIELD_COLORS

        assert len(set(PREVIEW_FIELD_COLORS)) == 10
