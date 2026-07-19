#!/usr/bin/env python3
"""pre-commit hook: 阻止未包裹 tr() 的 UI 字符串提交。

两层检查：
  Layer 1 — 中文未包裹：含中文字符的字符串常量 → AST 向上查是否被 tr() 包裹。
  Layer 2 — 英文 UI 控件未包裹：Qt widget 构造函数 / _form_row / setter 的首个字符串参数
            是否被 tr() 包裹。

检查 gui/ + cli/ 目录（共享 .ts 翻译源），其他模块放行。

豁免规则（优先级从高到低）：
  A. 被 tr() / self.tr() / QT_TR_NOOP() / QT_TRANSLATE_NOOP() 包裹
  B. 仅检查 gui/ + cli/ 目录
  C. 豁免文件：_i18n.py / __init__.py
  D. 测试文件
  E. Docstring
  F. 注释行 / 三引号字符串块
  G. 行级上下文豁免（log/raise/sys.exit/assert；GUI 附加 print/argparse）
  H. 行末 # i18n: allow 注释（单行豁免）
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

CHINESE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")
ENGLISH_LETTERS = re.compile(r"[a-zA-Z].*[a-zA-Z]")  # at least 2 ASCII letters

# ── 豁免规则 ─────────────────────────────────────────────────────────────
_CHECK_DIRS = ("astrocrawl/astrocrawl/gui", "astrocrawl/astrocrawl/cli")
_EXEMPT_FILES = {"_i18n.py", "__init__.py"}

# 行级豁免 patterns
_EXEMPT_COMMON = re.compile(
    r"(?:_log|_LOG|logging|logger)\."
    r"|raise\s+\w+(?:Error|Exception|Warning)\("
    r"|StartupError\(|ConfigValidationError\(|ValueError\("
    r"|sys\.exit\(|assert\s"
)
_EXEMPT_GUI_ONLY = re.compile(r"print\(|add_argument\(|help=")

# 单行豁免注释
_LINE_ALLOW = re.compile(r"#\s*i18n:\s*allow")

# ── AST 工具 ──────────────────────────────────────────────────────────────

_STATEMENT_BOUNDARIES = (
    ast.Module,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Return,
    ast.Assign,
    ast.AnnAssign,
    ast.AugAssign,
    ast.Expr,
    ast.If,
    ast.For,
    ast.While,
    ast.With,
    ast.Try,
    ast.Raise,
    ast.Assert,
    ast.Delete,
    ast.Match,
)

# UI 入口函数名 — 第一个字符串参数应被 tr() 包裹
_UI_CONSTRUCTORS = frozenset(
    {
        "QLabel",
        "QGroupBox",
        "QPushButton",
        "QCheckBox",
        "QRadioButton",
        "QAction",
        "QMenu",
    }
)
_UI_SETTERS = frozenset(
    {
        "setWindowTitle",
        "setText",
        "setToolTip",
        "setTitle",
        "setHeaderLabel",
    }
)


def _build_parents(tree: ast.AST) -> None:
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._parent = parent  # type: ignore[attr-defined]


def _is_tr_call(func: ast.expr) -> bool:
    match func:
        case ast.Name(id="tr" | "QT_TR_NOOP" | "QT_TRANSLATE_NOOP"):
            return True
        case ast.Attribute(attr="tr"):
            return True
    return False


def _is_docstring(node: ast.AST) -> bool:
    parent = getattr(node, "_parent", None)
    if not isinstance(parent, ast.Expr):
        return False
    grandparent = getattr(parent, "_parent", None)
    if not isinstance(grandparent, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    body = grandparent.body
    return len(body) > 0 and body[0] is parent


def _is_inside_tr(node: ast.AST) -> bool:
    current = node
    for _ in range(4):
        parent = getattr(current, "_parent", None)
        if parent is None:
            return False
        if isinstance(parent, ast.Call) and _is_tr_call(parent.func):
            if current in parent.args:
                return True
            for kw in parent.keywords:
                if current is kw.value:
                    return True
        if isinstance(parent, (ast.JoinedStr, ast.FormattedValue)):
            current = parent
            continue
        if isinstance(parent, ast.Call):
            return False
        if isinstance(parent, _STATEMENT_BOUNDARIES):
            return False
        current = parent
    return False


def _collect_chinese_constants(tree: ast.AST) -> list[ast.Constant]:
    result: list[ast.Constant] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if CHINESE.search(node.value):
                result.append(node)
    return result


# ── Layer 2: English UI strings ────────────────────────────────────────────


def _is_inside_call_of_interest(node: ast.Constant) -> bool:
    """判断 *node* 是否作为第一个参数传给 _form_row / QLabel / setter 等 UI 入口。"""
    parent = getattr(node, "_parent", None)
    if not isinstance(parent, ast.Call):
        return False
    if parent.args and parent.args[0] is not node:
        return False

    func = parent.func
    # _form_row(...) — module-level or nested function
    if isinstance(func, ast.Name) and func.id == "_form_row":
        return True
    # self._form_row(...) / cls._form_row(...)
    if isinstance(func, ast.Attribute) and func.attr == "_form_row":
        return True
    # QLabel(...) etc.
    if isinstance(func, ast.Name) and func.id in _UI_CONSTRUCTORS:
        return True
    # self.setText(...) / obj.setWindowTitle(...)
    if isinstance(func, ast.Attribute) and func.attr in _UI_SETTERS:
        return True

    return False


def _collect_english_ui_strings(tree: ast.AST) -> list[ast.Constant]:
    """收集作为 UI 控件首个参数但未被 tr() 包裹的英文字符串常量。"""
    result: list[ast.Constant] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        if not isinstance(node.value, str):
            continue
        text = node.value
        # 空字符串不报警（占位 QLabel("") 等）
        if not text.strip():
            continue
        # 只检查含英文的字符串（纯符号/纯数字不报）
        if not ENGLISH_LETTERS.search(text):
            continue
        # 已被 tr() 包裹 → 通过
        if _is_inside_tr(node):
            continue
        # 必须是 UI 入口的第一个参数
        if not _is_inside_call_of_interest(node):
            continue
        result.append(node)
    return result


# ── 主检查逻辑 ───────────────────────────────────────────────────────────


def _exemption_pattern(filepath: Path) -> re.Pattern[str] | None:
    path_str = str(filepath)
    if any(d in path_str for d in _CHECK_DIRS):
        if "astrocrawl/astrocrawl/cli" in path_str:
            return _EXEMPT_COMMON
        return re.compile(_EXEMPT_COMMON.pattern + r"|" + _EXEMPT_GUI_ONLY.pattern)
    return None


def _check_string_nodes(
    nodes: list[ast.Constant],
    filepath: Path,
    source_lines: list[str],
    exempt_ctx: re.Pattern[str] | None,
    layer_name: str,
) -> list[str]:
    """通用检查：遍历字符串节点，返回未通过 tr() 检查的违规列表。"""
    errors: list[str] = []
    for node in nodes:
        if _is_docstring(node):
            continue

        line = source_lines[node.lineno - 1]
        stripped = line.strip()

        if stripped.startswith("#"):
            continue
        if stripped.startswith(('"""', "'''")):
            continue
        if exempt_ctx and exempt_ctx.search(line):
            continue
        if _LINE_ALLOW.search(line):
            continue

        preview = node.value.replace("\n", "\\n")
        errors.append(f"{filepath}:{node.lineno}: [{layer_name}] UI 字符串未包裹 tr(): {preview[:80]}")

    return errors


def check_file(filepath: Path) -> list[str]:
    errors: list[str] = []

    path_str = str(filepath)
    if not any(d in path_str for d in _CHECK_DIRS):
        return errors
    if filepath.name in _EXEMPT_FILES:
        return errors
    if "test" in filepath.name.lower() or "/tests/" in path_str or path_str.startswith("tests/"):
        return errors

    try:
        content = filepath.read_text(encoding="utf-8")
        tree = ast.parse(content)
    except SyntaxError:
        return errors

    _build_parents(tree)
    source_lines = content.splitlines()
    exempt_ctx = _exemption_pattern(filepath)

    # Layer 1: Chinese strings
    chinese_nodes = _collect_chinese_constants(tree)
    errors.extend(_check_string_nodes(chinese_nodes, filepath, source_lines, exempt_ctx, "中文"))

    # Layer 2: English UI strings
    english_nodes = _collect_english_ui_strings(tree)
    errors.extend(_check_string_nodes(english_nodes, filepath, source_lines, exempt_ctx, "英文UI"))

    return errors


def main() -> int:
    errors: list[str] = []
    for filename in sys.argv[1:]:
        filepath = Path(filename)
        if not filepath.exists():
            continue
        if filepath.suffix != ".py":
            continue
        errors.extend(check_file(filepath))

    if errors:
        print("i18n: 发现未包裹 tr() 的 UI 字符串:\n")
        for e in errors:
            print(f"  {e}")
        print(f"\n  {len(errors)} 处违规。请用 tr() 包裹。")
        print('  若确系日志/异常/内部信息，在行末添加 "# i18n: allow" 即可放行。')
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
