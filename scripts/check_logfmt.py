#!/usr/bin/env python3
"""pre-commit hook: 阻止不合规的日志调用提交。

检查 astrocrawl/ 下所有 Python 文件中的日志调用，确保：
  - 使用 logging.getLogger() 创建的 logger，其消息字符串以 "event=" 开头（logfmt 规范）
  - LogfmtLogger 实例调用不受影响（API 层已强制 event 参数）

豁免规则：
  A. LogfmtLogger 实例的方法调用 → 跳过（API 层强制）
  B. 模块级 logging.xxx() 直接调用（非 getLogger 结果） → 跳过（启动期日志）
  C. 测试文件（路径含 /tests/） → 跳过
  D. 行末 # logfmt: allow 注释 → 跳过该行
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_LOG_METHODS = frozenset({"debug", "info", "warning", "error", "critical", "exception"})


def _has_logfmtlogger_import(tree: ast.AST) -> bool:
    """检查文件是否导入了 LogfmtLogger（用于判断是否需要追踪 LogfmtLogger 实例）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "astrobasis":
                for alias in node.names:
                    if alias.name == "LogfmtLogger":
                        return True
    return False


def _collect_loggers(tree: ast.AST, has_logfmtlogger_import: bool) -> tuple[set[str], set[str], set[str]]:
    """收集 logger 变量。

    Returns:
        (stdlib_loggers, logfmt_loggers, self_loggers)
        - stdlib_loggers: 模块级赋值为 logging.getLogger(...) 的变量名
        - logfmt_loggers: 模块级赋值为 LogfmtLogger(...) 的变量名
        - self_loggers: __init__ 中赋值为 logging.getLogger(...) 的 self.xxx 属性名
    """
    stdlib_loggers: set[str] = set()
    logfmt_loggers: set[str] = set()
    self_loggers: set[str] = set()

    for node in ast.walk(tree):
        # 模块级: name = logging.getLogger(...)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if _is_getlogger_call(node.value, "logging"):
                        stdlib_loggers.add(target.id)
                    elif has_logfmtlogger_import and _is_logfmtlogger_call(node.value):
                        logfmt_loggers.add(target.id)

        # __init__ 中: self.xxx = logging.getLogger(...)
        if isinstance(node, ast.FunctionDef) and node.name == "__init__":
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                            if target.value.id == "self" and _is_getlogger_call(stmt.value, "logging"):
                                self_loggers.add(target.attr)

    return stdlib_loggers, logfmt_loggers, self_loggers


def _is_getlogger_call(node: ast.expr, module_name: str) -> bool:
    """检查是否为 module_name.getLogger(...) 调用。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr != "getLogger":
        return False
    return isinstance(func.value, ast.Name) and func.value.id == module_name


def _is_logfmtlogger_call(node: ast.expr) -> bool:
    """检查是否为 LogfmtLogger(...) 调用。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return isinstance(func, ast.Name) and func.id == "LogfmtLogger"


def _is_log_call(node: ast.expr, logger_names: set[str], self_logger_attrs: set[str]) -> bool:
    """检查是否为 stdlib logger 的日志方法调用。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in _LOG_METHODS:
        return False

    # 模块级: _log.info(...) / logger.info(...) / _LOG.info(...)
    if isinstance(func.value, ast.Name) and func.value.id in logger_names:
        return True

    # 实例级: self._log.info(...)
    if isinstance(func.value, ast.Attribute) and isinstance(func.value.value, ast.Name):
        if func.value.value.id == "self" and func.value.attr in self_logger_attrs:
            return True

    return False


def _check_message(first_arg: ast.expr) -> tuple[bool, str]:
    """检查第一个参数是否以 event= 开头。

    Returns:
        (is_ok, issue_description)
    """
    # 字符串常量
    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
        if first_arg.value.startswith("event="):
            return True, ""
        preview = first_arg.value[:60].replace("\n", " ")
        return False, f'日志消息缺少 event= 前缀: "{preview}..."' if len(
            first_arg.value
        ) > 60 else f'日志消息缺少 event= 前缀: "{first_arg.value}"'

    # f-string: 提取静态前缀检查 event=
    if isinstance(first_arg, ast.JoinedStr):
        prefix_parts: list[str] = []
        for part in first_arg.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                prefix_parts.append(part.value)
            else:
                break
        prefix = "".join(prefix_parts)
        if "event=" in prefix:
            return True, ""
        return False, f'f-string 缺少 event= 前缀: "{prefix}..."'

    # .format() 调用
    if isinstance(first_arg, ast.Call) and isinstance(first_arg.func, ast.Attribute):
        if first_arg.func.attr == "format":
            if isinstance(first_arg.func.value, ast.Constant) and isinstance(first_arg.func.value.value, str):
                template = first_arg.func.value.value
                if template.startswith("event="):
                    return True, ""
                return False, f'.format() 模板缺少 event= 前缀: "{template[:60]}"'

    # 变量引用 — 无法静态检查
    if isinstance(first_arg, ast.Name):
        return True, ""

    return True, ""


def _is_module_level_logging(node: ast.expr) -> bool:
    """检查是否为模块级 logging.xxx() 调用（非 getLogger 的结果）。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in _LOG_METHODS:
        return False
    return isinstance(func.value, ast.Name) and func.value.id == "logging"


def _has_allow_comment(node: ast.AST, source_lines: list[str]) -> bool:
    """检查节点所在行是否有 # logfmt: allow 注释。"""
    if hasattr(node, "lineno") and node.lineno is not None:
        line = source_lines[node.lineno - 1]
        return "# logfmt: allow" in line
    return False


def check_file(filepath: Path) -> list[str]:
    """检查单个文件，返回违规列表。"""
    try:
        source = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    source_lines = source.splitlines()
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    has_logfmtlogger = _has_logfmtlogger_import(tree)
    stdlib_loggers, logfmt_loggers, self_loggers = _collect_loggers(tree, has_logfmtlogger)

    violations: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # 豁免: 模块级 logging.xxx() 调用
        if _is_module_level_logging(node):
            continue

        # 豁免: LogfmtLogger 实例的方法调用
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id in logfmt_loggers:
                continue

        # 检查是否为 stdlib logger 的日志调用
        if not _is_log_call(node, stdlib_loggers, self_loggers):
            continue

        # 提取第一个参数
        if not node.args:
            continue

        first_arg = node.args[0]

        # 豁免: 行末 # logfmt: allow
        if _has_allow_comment(node, source_lines):
            continue

        ok, msg = _check_message(first_arg)
        if not ok:
            violations.append(f"{filepath}:{node.lineno}: [LOGFMT] {msg}")

    return violations


def main() -> int:
    files = [
        Path(f)
        for f in sys.argv[1:]
        if f.endswith(".py")
        and ("/astrocrawl/" in f or "/astrobasis/" in f or "/astroframe/" in f or "/astroflow/" in f)
    ]

    # 跳过测试文件
    files = [f for f in files if "/tests/" not in str(f)]

    if not files:
        return 0

    all_violations: list[str] = []
    for filepath in files:
        all_violations.extend(check_file(filepath))

    if all_violations:
        for v in all_violations:
            print(v)
        print(f"\n{len(all_violations)} violations in {len({v.split(':')[0] for v in all_violations})} files")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
