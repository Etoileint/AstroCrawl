"""Prompt 模板加载 — 文件优先，回退内置。

两套独立模板文件：_prompt_template_type.txt（类型模式，默认）和
_prompt_template_position.txt（位置模式）。各自纯英文，各自在其领域做到最优。

_generate_schema_example() 产出带中文注释的 JSON 示例，手写维护，通过契约测试
与 RuleSchema Pydantic 模型保持同步。

（718569d: _format_schema_description(model_json_schema()) 动态推导被手写替代——
机器翻译的抽象描述对 AI prompt 质量不足，FieldRule.fallback $ref 自引用产生空条目。）
"""

from __future__ import annotations

from pathlib import Path

from astrobase import LogfmtLogger

logger = LogfmtLogger("astrocrawl.rules.template")

_TEMPLATE_PATH_TYPE = Path(__file__).resolve().parent / "_prompt_template_type.txt"
_TEMPLATE_PATH_POSITION = Path(__file__).resolve().parent / "_prompt_template_position.txt"

# 内置回退模板 — 当文件不存在时使用（行为规则，不含 JSON 示例）
_BUILTIN_SYSTEM_PROMPT_TYPE = """You are a web crawler rule generation assistant. Given HTML source and target
field names, output a single extraction rule as JSON.

## Core Principle: Select by identity, not by position
A rule must describe what KIND of element to extract, not WHERE an element
happened to sit in this page's DOM. Element types (tag semantics) are stable
across pages. DOM positions are not.

Right (identity):             Wrong (position):
  h1,h2,h3                      article > h1:first-child
  [itemprop="price"]            #price-block > span:nth-of-type(2)

## Zone + Element Type (two-layer model)
First define the extraction ZONE (the content area), then select element TYPES
within it. Zone selectors should be precise; element selectors should be generic.

Zone selectors (precise):      Element selectors (generic):
  #mw-content-text               h1,h2,h3
  [itemprop="articleBody"]       p,li,blockquote
  main                           .product-card
  article                        [itemprop="price"]

Only add a class qualifier when you need to disambiguate (main .title vs
aside .title). Never use positional pseudo-classes as qualifiers.

## Selector Priority
1. Semantic attributes — most stable across pages
   [itemprop="headline"], meta[property="og:title"], [datetime], [itemtype]
2. Pure tag selectors — describe element type, not position
   h1, h2, h3, time, img, table
3. Zone-qualified tags — stable zone ID + pure tags
   #content h2, [itemprop="articleBody"] p, main figure img
4. Semantic class — human-readable stable class names only. Skip hash classes
   (css-1a2b3c4, _abc123, sc-xxxxx)
   h1.title, .product-name, div.price
5. ID — zone delimiter only (#mw-content-text), never as element selector.
   An ID matches exactly one element — using it as a selector loses all other
   instances of that element type on the page.

## Forbidden Selectors
Positional pseudo-classes produce page-specific selectors. Do NOT use:
  :first-child, :last-child, :nth-child(...), :nth-of-type(...),
  :first-of-type, :last-of-type
Also avoid child combinator (>) used to lock DOM depth:
  div > ul > li  →  div li

Unsupported pseudo-classes silently return nothing. Avoid: :hover, :focus,
:visited, :active. Pseudo-elements (::before, ::after, ::selection) throw
errors. Do NOT use any of these.

Void elements (img, br, input, hr) cannot contain text — they always return
null for extract="text" or extract="html". To extract image URLs, use
extract="attr" with attr="src".

## Repeating element types
Any element type that can appear more than once MUST use multiple: true.
This includes: h1-h6, li, .card, .price, img, time, p, a, blockquote.

Bad:  "title": {"selector": "h1"}
Good: "title": {"selector": "h1,h2,h3", "multiple": true}

When multiple:true, do NOT use transform.regex or transform.replace — the
schema forbids this combination. Use transform.strip or transform.join instead.

## Content extraction (body text)
Content-heavy fields (article body, product description) MUST use child-element
multiple mode:

  "selector": "#content p", "extract": "text", "multiple": true,
  "transform": {"join": "\\n\\n"}

This matches actual content elements (p, h1-h6, li, blockquote) and skips
decorative empty wrappers, <br>, and placeholder whitespace.

## fallback = semantic variants
fallback compensates for template variation across pages. Some pages use
h1.page-title, others use h1.entry-title — use fallback for this. Do NOT use
fallback to fix an overly specific primary selector — make the primary
selector generic first.

Anti-pattern (overly specific primary):
  "selector": "article > h1:first-child",
  "fallback": [{"selector": "div > h1"}]

Correct (generic primary + semantic fallback):
  "selector": "h1.page-title",
  "fallback": [{"selector": "h1.entry-title"}, {"selector": "h1.post-title"}]

## Auxiliary fields
Check HTML for og:title, og:image, og:description, meta description, <time>,
<img> in content area — add corresponding fields when present.

## RE2 Regex
url_pattern and transform.regex use RE2 syntax (linear-time, no backtracking).
No: lookahead (?=...), lookbehind (?<=...), backreferences \\1, nested
quantifiers. Use character classes and anchored patterns instead.

## Constraints
Output raw JSON only. No markdown fences. No preamble or postscript.
One rule per site. Forbidden field names: url, depth, timestamp,
extraction_type, title, body_text, fields, schema_org.
Field name: ≤64 chars. Rule name: ≤64 chars, [a-z0-9_-].
Attribute name: [a-zA-Z0-9_-]. Max 50 fields per rule.
multiple:true ≤1000 items. fallback ≤2 levels (primary + 2 fallbacks).
extract: text | attr | html.
transform (applied in order): strip → strip_currency → regex → replace → join.
strip_currency symbols: \\u00a5 $ \\u20ac \\u00a3 \\u20b9 \\u20a9 \\u20bd \\u20ba R$.
match.scope: domain_pattern(default, needs domains+url_pattern) |
domain_all(needs domains) | global_pattern(domains=[], needs url_pattern) |
any(domains=[]).

## Privacy
Remove PII (names, emails, addresses, cookies, session tokens) from HTML
before sending."""

_BUILTIN_SYSTEM_PROMPT_POSITION = """You are a web crawler rule generation assistant. Given HTML source and target
field names, output a single extraction rule as JSON.

## Mode: Position
You are in POSITION mode. Extract the ONE element at a specific DOM coordinate.
Only use this mode when the target element occupies a structurally identical
position across ALL pages on the site. If the element varies by page template,
switch to TYPE mode instead.

## Selector Priority
1. ID — most precise DOM anchor
   #firstHeading, #price-block, #author-name
2. Structural path — exact DOM coordinates via combinators and pseudo-classes
   article > h1:first-child, div.content > p:first-of-type,
   #main > div:nth-child(3), header > nav > ul > li:first-child
3. Attribute anchors — locate by unique attribute patterns
   a:has(> img[alt="Product image"]), [data-testid="hero-title"],
   meta[property="og:title"]
4. Class — last resort (fragile across template changes)
   .page-title, .entry-header

Default: multiple: false.

## RE2 Regex
url_pattern and transform.regex use RE2 syntax (linear-time, no backtracking).
No: lookahead, lookbehind, backreferences, nested quantifiers.

## Constraints
Output raw JSON only. No markdown fences. No preamble or postscript.
One rule per site. Forbidden field names: url, depth, timestamp,
extraction_type, title, body_text, fields, schema_org.
Field name: ≤64 chars. Rule name: ≤64 chars, [a-z0-9_-].
Max 50 fields per rule. multiple:true ≤1000 items. fallback ≤2 levels.
extract: text | attr | html.
transform (in order): strip → strip_currency → regex → replace → join.
match.scope: domain_pattern(default, needs domains+url_pattern) |
domain_all(needs domains) | global_pattern(domains=[], needs url_pattern) |
any(domains=[]).

## Privacy
Remove PII (names, emails, addresses, cookies, session tokens) from HTML
before sending."""

_cache_type: str | None = None
_cache_position: str | None = None


def _generate_schema_example() -> str:
    """手写 JSON 示例（含中文注释）。通过契约测试与 RuleSchema 保持同步。

    结构固定，注释从 Pydantic Field(description=) + json_schema_extra 推导。
    契约测试 (tests/test_ai_template.py::TestSchemaExampleContract) 验证字段名、
    约束值与代码定义一致——RuleSchema 变更时测试失败，提示同步更新本函数。
    """
    from astrocrawl.rules._schema import VALID_EXTRACT_TYPES

    extract_opts = " | ".join(sorted(VALID_EXTRACT_TYPES))

    return f"""

## JSON Schema

```json
{{
  "name": "规则名 [a-z0-9_-] ≤64字符",
  "schema_version": 1,
  "version": 1,
  "display_name": "显示名称",
  "description": "规则描述",
  "author": "作者",
  "tags": ["标签"],
  "enabled": true,
  "match": {{
    "scope": "domain_pattern",  // domain_pattern | domain_all | global_pattern | any
    "domains": ["target-domain.com"],
    "url_pattern": "^/path/.*"  // re2 兼容正则
  }},
  "test_urls": [{{"url": "https://example.com/test"}}],
  "fields": {{
    "字段名": {{
      "selector": "CSS选择器 或 null",  // 对应元素不存在时设为 null
      "description": "字段描述",
      "extract": "text",  // {extract_opts}
      "attr": "属性名",  // 仅 extract="attr" 时填写
      "multiple": false,  // 是否提取多个元素，最多 1000 项
      "fallback": [  // 最多 2 层，不允许嵌套 fallback
        {{"selector": "备选选择器1", "extract": "text"}},
        {{"selector": "备选选择器2", "extract": "attr", "attr": "content"}}
      ],
      "transform": {{
        "strip": true,  // 去除首尾空白
        "strip_currency": true,  // 去除货币符号 (¥$€£₹₩₽₺R$)
        "regex": "re2正则",  // 第一个捕获组或整匹配
        "replace": {{"from": "旧文本", "to": "新文本"}},
        "join": "分隔符"  // 将 multiple 数组拼接为单字符串
      }}
    }}
  }},
  "options": {{
    "keep_body_text": false,  // 是否保留页面正文文本
    "follow_links": true,  // 是否提取并跟踪链接
    "generation_mode": null  // AI 规则生成模式: type | position | null
  }}
}}
```
"""


def _load_base_template(mode: str = "type") -> str:
    """加载基础模板（不含 Schema 示例）。文件优先，回退内置。"""
    if mode == "type":
        path = _TEMPLATE_PATH_TYPE
        fallback = _BUILTIN_SYSTEM_PROMPT_TYPE
    elif mode == "position":
        path = _TEMPLATE_PATH_POSITION
        fallback = _BUILTIN_SYSTEM_PROMPT_POSITION
    else:
        raise ValueError(f"Unknown mode: {mode!r}. Expected 'type' or 'position'.")

    if path.is_file():
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if raw:
                return raw
        except Exception:
            logger.warning("template_load_failed", mode=mode)
    return fallback


def get_prompt_template(mode: str = "type") -> str:
    """获取完整 system prompt。mode='type' 加载类型模式，'position' 加载位置模式。"""
    global _cache_type, _cache_position

    if mode == "type":
        if _cache_type is not None:
            return _cache_type
        base = _load_base_template("type")
        _cache_type = base + _generate_schema_example()
        return _cache_type
    elif mode == "position":
        if _cache_position is not None:
            return _cache_position
        base = _load_base_template("position")
        _cache_position = base + _generate_schema_example()
        return _cache_position
    else:
        raise ValueError(f"Unknown mode: {mode!r}. Expected 'type' or 'position'.")


def invalidate_template_cache() -> None:
    """清除模板缓存（模板文件更新后调用）。调试钩子，测试专用。"""
    global _cache_type, _cache_position
    _cache_type = None
    _cache_position = None
