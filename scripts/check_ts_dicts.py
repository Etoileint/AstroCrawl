#!/usr/bin/env python3
"""TS 字典覆盖率检查 — 补 lupdate 无法追踪 tr(variable) 的盲区。

两层检查：
1. 已注册字典 → TS 交叉比对（主动覆盖）
2. 扫描所有 self.tr(变量) 调用点 → 与 REGISTRY 比对（防遗漏）
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

# ── 注册字典: (Python 文件, 变量名, TS context 列表) ────────────────────────

REGISTRY = [
    ("astrocrawl/astrocrawl/proxy/_consumers.py", "PROXY_CONSUMERS", ["ProxyProfileListModel", "ProxyRouteModel"]),
    ("astrocrawl/astrocrawl/gui/rules_dialog.py", "_TIER_LABELS", ["_CustomPage"]),
    ("astrocrawl/astrocrawl/gui/theme_dialog.py", "_TOKEN_LABELS", ["ThemeDialog"]),
]

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


def _build_parents(tree: ast.AST) -> None:
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            child._parent = parent  # type: ignore[attr-defined]


def _extract_dict_values(filepath: Path, var_name: str) -> list[str]:
    try:
        content = filepath.read_text(encoding="utf-8")
        tree = ast.parse(content)
    except (SyntaxError, OSError):
        return []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            for target in targets:
                if isinstance(target, ast.Name) and target.id == var_name:
                    if isinstance(node.value, ast.Dict):
                        return _dict_values(node.value)
                    if isinstance(node.value, ast.List):
                        return _list_of_tuples_values(node.value)
    return []


def _dict_values(node: ast.Dict) -> list[str]:
    result: list[str] = []
    for v in node.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            result.append(v.value)
    return result


def _list_of_tuples_values(node: ast.List) -> list[str]:
    result: list[str] = []
    for elt in node.elts:
        if isinstance(elt, ast.Tuple) and len(elt.elts) >= 2:
            v = elt.elts[1]
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                result.append(v.value)
    return result


def _ts_has_entry(ts_path: Path, context: str, source: str) -> bool:
    try:
        tree = ET.parse(ts_path)
    except (ET.ParseError, OSError):
        return False
    for ctx in tree.findall("context"):
        name = ctx.find("name")
        if name is None or name.text != context:
            continue
        for msg in ctx.findall("message"):
            src = msg.find("source")
            if src is not None and src.text == source:
                return True
    return False


def _enclosing_class(node: ast.AST) -> str:
    """向上查找包含 *node* 的 QObject 类名。"""
    current = node
    while current is not None:
        if isinstance(current, ast.ClassDef):
            return current.name
        current = getattr(current, "_parent", None)
    return "(module)"


def _trace_var_source(node: ast.AST, var_name: str) -> str | None:
    """在同一个方法/函数体内，尝试追踪 *var_name* 的来源字典名。

    支持: for var_name in DICT / for _, var_name in DICT / [expr for var_name in DICT]
    返回字典变量名，无法追踪则返回 None。
    """
    # 找到包含该 tr() 调用的方法/函数
    func = node
    while func is not None and not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
        func = getattr(func, "_parent", None)
    if func is None:
        return None

    # 在方法体内找 for 循环或 comprehension，其中循环变量匹配 var_name
    for child in ast.walk(func):
        if isinstance(child, ast.For) and child.iter is not None:
            target = child.target
            source = _resolve_iter_source(child.iter, target, var_name)
            if source:
                return source

        # List comprehension: [self.tr(var_name) for ... in DICT]
        if isinstance(child, ast.ListComp):
            for gen in child.generators:
                if isinstance(gen.target, ast.Name) and gen.target.id == var_name:
                    source = _resolve_iter_source(gen.iter, gen.target, var_name)
                    if source:
                        return source

    return None


def _resolve_iter_source(iter_node: ast.expr, target: ast.expr, var_name: str) -> str | None:
    """从 for 循环的 iter 表达式中提取字典变量名。"""
    # for var_name in DICT
    if isinstance(target, ast.Name) and target.id == var_name:
        if isinstance(iter_node, ast.Name):
            return iter_node.id

    # for key, var_name in DICT.items()  /  for var_name in DICT.values()
    if isinstance(target, ast.Tuple):
        for elt in target.elts:
            if isinstance(elt, ast.Name) and elt.id == var_name:
                if isinstance(iter_node, ast.Call) and isinstance(iter_node.func, ast.Attribute):
                    if iter_node.func.attr in ("items", "values"):
                        if isinstance(iter_node.func.value, ast.Name):
                            return iter_node.func.value.id

    return None


def _find_unregistered_tr_vars(repo_root: Path) -> list[str]:
    """扫描所有 GUI 文件中的 self.tr(variable) 调用，检查是否已被 REGISTRY 覆盖。"""
    warnings: list[str] = []
    gui_dir = repo_root / "astrocrawl/astrocrawl/gui"
    if not gui_dir.is_dir():
        return warnings

    # 构建 REGISTRY 覆盖的 context 集合（用于跳过已验证的上下文）
    covered_contexts: set[str] = set()
    for _rel, _var_name, contexts in REGISTRY:
        covered_contexts.update(contexts)

    for py_file in gui_dir.rglob("*.py"):
        name = py_file.name
        if "test" in name.lower() or name.startswith("__"):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue

        _build_parents(tree)

        for node in ast.walk(tree):
            # self.tr(NAME) where NAME is not a string literal
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "tr":
                continue
            if not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id != "self":
                continue
            if not node.args:
                continue
            arg = node.args[0]
            if not isinstance(arg, ast.Name):
                continue  # string literal → lupdate handles it

            var_name = arg.id
            context = _enclosing_class(node)
            rel = py_file.relative_to(repo_root)

            # Context 已在 REGISTRY 覆盖范围内 → Layer 1 已验证 TS 条目完备
            if context in covered_contexts:
                continue

            source_dict = _trace_var_source(node, var_name)

            if source_dict:
                warnings.append(
                    f"{rel}:{node.lineno}: self.tr({var_name!r}) 来源 {source_dict!r}"
                    f" (context={context}) — 请在 REGISTRY 中注册此字典"
                )
            else:
                warnings.append(
                    f"{rel}:{node.lineno}: self.tr({var_name!r}) 无法追踪来源"
                    f" (context={context}) — 请在 REGISTRY 中注册"
                )

    return warnings


# ── main ────────────────────────────────────────────────────────────────────


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    ts_path = repo_root / "astrocrawl/astrocrawl/gui/translations/astrocrawl_gui_zh_CN.ts"

    # --fix-vanished mode
    if len(sys.argv) == 3 and sys.argv[1] == "--fix-vanished":
        _fix_vanished(repo_root, Path(sys.argv[2]))
        return 0

    errors: list[str] = []

    # Layer 1: Registered dicts → TS coverage
    for rel_path, var_name, contexts in REGISTRY:
        filepath = repo_root / rel_path
        values = _extract_dict_values(filepath, var_name)
        for value in values:
            for ctx in contexts:
                if not _ts_has_entry(ts_path, ctx, value):
                    errors.append(f'{rel_path}: {var_name} 值 "{value}" 在 TS context "{ctx}" 中缺失翻译')

    # Layer 2: Unregistered self.tr(variable) call sites
    warnings = _find_unregistered_tr_vars(repo_root)

    if errors or warnings:
        if errors:
            print(f"TS 字典覆盖率: {len(errors)} 条缺失翻译条目:\n")
            for e in errors:
                print(f"  {e}")
        if warnings:
            print(f"\n未注册的 self.tr(变量) 调用点: {len(warnings)} 处:\n")
            for w in warnings:
                print(f"  {w}")
            print("\n  请在 REGISTRY 中注册这些字典。")
        return 1

    print("TS 字典覆盖率: 全部通过")
    return 0


def _fix_vanished(repo_root: Path, ts_path: Path) -> None:
    all_values: set[str] = set()
    for rel_path, var_name, _contexts in REGISTRY:
        filepath = repo_root / rel_path
        all_values.update(_extract_dict_values(filepath, var_name))

    try:
        tree = ET.parse(ts_path)
    except (ET.ParseError, OSError):
        return

    restored = 0
    for ctx in tree.findall("context"):
        for msg in ctx.findall("message"):
            trans = msg.find("translation")
            src = msg.find("source")
            if trans is not None and src is not None and src.text in all_values:
                if trans.get("type") == "vanished":
                    del trans.attrib["type"]
                    restored += 1

    if restored:
        ET.indent(tree.getroot(), space="    ")
        ts_path.write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n' + ET.tostring(tree.getroot(), encoding="unicode"),
            encoding="utf-8",
        )
        print(f"dict-ts: restored {restored} vanished entries")
    else:
        print("dict-ts: no vanished entries to restore")


if __name__ == "__main__":
    sys.exit(main())
