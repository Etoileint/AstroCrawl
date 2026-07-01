"""解析规则引擎 — 结构化内容提取。

加载 → 匹配 → CSS 提取 → Transform → 输出。
"""

from __future__ import annotations

from astrocrawl.rules._ai import RuleGenerator, get_assembled_prompt
from astrocrawl.rules._chatml import count_tokens, serialize_chatml
from astrocrawl.rules._extractor import extract_fields, extract_fields_from_soup
from astrocrawl.rules._html_preprocess import PreprocessTier, preprocess_html
from astrocrawl.rules._io import (
    cleanup_tmp_files,
    export_all_rules,
    export_rule_to_file,
    import_rule,
    import_rule_preview,
    rule_to_dict,
    safe_read_rule_file,
    safe_write_rule_file,
)
from astrocrawl.rules._lifecycle import RuleLifecycle, setup_rule_directories
from astrocrawl.rules._loader import (
    RuleConflictError,
    build_rule_snapshot,
    ensure_no_rule_conflicts,
    load_rule_file,
    validate_rule_files,
)
from astrocrawl.rules._markdown import clean_markdown_wrapper
from astrocrawl.rules._matcher import match_url, match_url_with_candidates
from astrocrawl.rules._schema import FieldRule, MatchConfig, MatchScope, RuleOptions, RuleSchema, validate_rule
from astrocrawl.rules._source import (
    SourceManager,
    add_source_to_file,
    get_source_from_file,
    list_sources_from_file,
    remove_source_from_file,
    update_source_in_file,
    validate_source_url,
)
from astrocrawl.rules._state import set_rule_enabled, set_rules_enabled
from astrocrawl.rules._template import get_prompt_template
from astrocrawl.rules._transform import apply_transforms

__all__ = [
    # Schema
    "RuleSchema",
    "FieldRule",
    "MatchConfig",
    "MatchScope",
    "RuleOptions",
    "validate_rule",
    # Loader
    "build_rule_snapshot",
    "ensure_no_rule_conflicts",
    "load_rule_file",
    "validate_rule_files",
    "RuleConflictError",
    # Matcher
    "match_url",
    "match_url_with_candidates",
    # Extractor
    "extract_fields",
    "extract_fields_from_soup",
    # Transform
    "apply_transforms",
    # Template
    "get_prompt_template",
    # AI
    "RuleGenerator",
    "get_assembled_prompt",
    # HTML Preprocess
    "preprocess_html",
    "PreprocessTier",
    # ChatML
    "serialize_chatml",
    "count_tokens",
    # Lifecycle
    "RuleLifecycle",
    "setup_rule_directories",
    # Source
    "SourceManager",
    "add_source_to_file",
    "get_source_from_file",
    "list_sources_from_file",
    "remove_source_from_file",
    "update_source_in_file",
    "validate_source_url",
    # Markdown
    "clean_markdown_wrapper",
    # I/O
    "cleanup_tmp_files",
    "export_all_rules",
    "export_rule_to_file",
    "import_rule",
    "import_rule_preview",
    "rule_to_dict",
    "safe_read_rule_file",
    "safe_write_rule_file",
    # State
    "set_rule_enabled",
    "set_rules_enabled",
]
