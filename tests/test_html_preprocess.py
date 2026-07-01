"""HTML 预处理测试 — 三级清洗 + 边缘情况。"""

from __future__ import annotations

from astrocrawl.rules._html_preprocess import PreprocessTier, preprocess_html


class TestPreprocessTier0:
    def test_returns_html_unchanged(self):
        html = "<html><script>alert(1)</script><body><p>hi</p></body></html>"
        assert preprocess_html(html, PreprocessTier.OFF) == html

    def test_empty_string_returns_empty(self):
        assert preprocess_html("", PreprocessTier.OFF) == ""


class TestPreprocessTier1:
    def test_removes_script_tags(self):
        result = preprocess_html(
            "<html><head></head><body><script>alert(1)</script><p>hi</p></body></html>",
            PreprocessTier.CANONICAL,
        )
        assert "alert" not in result
        assert "<p>hi</p>" in result

    def test_removes_style_tags(self):
        result = preprocess_html(
            "<html><head><style>.x{color:red}</style></head><body><p>hi</p></body></html>",
            PreprocessTier.CANONICAL,
        )
        assert ".x" not in result
        assert "<p>hi</p>" in result

    def test_removes_comments(self):
        result = preprocess_html(
            "<html><body><!-- comment --><p>hi</p></body></html>",
            PreprocessTier.CANONICAL,
        )
        assert "comment" not in result
        assert "<p>hi</p>" in result

    def test_removes_noscript(self):
        result = preprocess_html(
            "<html><body><noscript>no JS</noscript><p>hi</p></body></html>",
            PreprocessTier.CANONICAL,
        )
        assert "no JS" not in result
        assert "<p>hi</p>" in result

    def test_removes_svg_and_math(self):
        result = preprocess_html(
            "<html><body><svg><path d='M0'/></svg><p>hi</p><math><mi>x</mi></math></body></html>",
            PreprocessTier.CANONICAL,
        )
        assert "M0" not in result
        assert "<p>hi</p>" in result

    def test_removes_head_metadata(self):
        result = preprocess_html(
            "<html><head><meta charset='utf-8'><link rel='stylesheet'><title>T</title></head><body><p>hi</p></body></html>",
            PreprocessTier.CANONICAL,
        )
        assert "<p>hi</p>" in result
        assert "charset" not in result

    def test_preserves_body_content(self):
        result = preprocess_html(
            "<html><head><title>T</title></head><body><div class='main'><h1>Title</h1><p>Content</p></div></body></html>",
            PreprocessTier.CANONICAL,
        )
        assert "Title" in result
        assert "Content" in result
        assert "main" in result

    def test_preserves_data_attributes(self):
        result = preprocess_html(
            "<html><body><div data-id='123' data-name='test'>content</div></body></html>",
            PreprocessTier.CANONICAL,
        )
        assert "data-id" in result
        assert "123" in result

    def test_non_html_text_handled_gracefully(self):
        html = "not html at all >>>><<<<"
        result = preprocess_html(html, PreprocessTier.CANONICAL)
        assert "not html at all" in result

    def test_whitespace_only_returns_unchanged(self):
        result = preprocess_html("   \n  ", PreprocessTier.CANONICAL)
        assert result == "   \n  "


class TestPreprocessTier2:
    def test_removes_nav(self):
        result = preprocess_html(
            "<html><body><nav>nav</nav><p>content</p></body></html>",
            PreprocessTier.STRICT,
        )
        assert "nav" not in result.lower()
        assert "<p>content</p>" in result

    def test_removes_footer(self):
        result = preprocess_html(
            "<html><body><footer>footer</footer><p>content</p></body></html>",
            PreprocessTier.STRICT,
        )
        assert "footer" not in result
        assert "<p>content</p>" in result

    def test_removes_aside(self):
        result = preprocess_html(
            "<html><body><aside>sidebar</aside><p>content</p></body></html>",
            PreprocessTier.STRICT,
        )
        assert "sidebar" not in result
        assert "<p>content</p>" in result

    def test_removes_header(self):
        result = preprocess_html(
            "<html><body><header>header</header><p>content</p></body></html>",
            PreprocessTier.STRICT,
        )
        assert "header" not in result
        assert "<p>content</p>" in result

    def test_includes_tier1_removals(self):
        result = preprocess_html(
            "<html><head><title>X</title></head><body><script>js</script><nav>nav</nav><p>content</p></body></html>",
            PreprocessTier.STRICT,
        )
        assert "js" not in result
        assert "nav" not in result.lower()
        assert "<p>content</p>" in result


class TestPreprocessSizeLimit:
    def test_exceeds_limit_returns_unchanged_with_warning(self, monkeypatch, caplog):
        monkeypatch.setattr("astrocrawl.rules._html_preprocess._MAX_INPUT_BYTES", 10)
        html = "<html><body><p>Hello World!</p></body></html>"
        result = preprocess_html(html, PreprocessTier.CANONICAL)
        assert result == html
        assert "event=html_preprocess_too_large" in caplog.text


class TestPreprocessErrorHandling:
    def test_null_byte_input_triggers_parser_error_fallback(self, caplog):
        html = "\x00"
        result = preprocess_html(html, PreprocessTier.CANONICAL)
        assert result == html
        assert "event=html_preprocess_parse_error" in caplog.text
