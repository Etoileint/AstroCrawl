"""AST + 文件系统双层安全扫描器（ADR-0011 S5）。

零 import 门控——通过 ast 模块静态分析 .py 文件 + os.scandir 检测二进制文件。
在插件代码进入进程前完成，是纵深防御的第一层。

检测机制（非名单、非经验规则）：
  Layer A — import 闭包追溯至因果根模块。每个危险能力的因果根在 Python 中是唯一的：
    socket → network.outbound    subprocess → process.spawn
    pickle → code.unpickle       marshal    → code.unpickle
    ctypes → code.ffi            cffi       → code.ffi
    递归扫描 stdlib 模块的 import 链，缓存已解析的 AST。新 Python 版本新增
    网络/子进程/序列化库 → 其 import 必然途经因果根 → 自动捕获，零维护。
  Layer B — AST 调用名匹配，仅覆盖无法用 import 追溯的语言原语：
    eval/exec/compile      → code.dynamic
    open()                 → filesystem.write
    breakpoint()           → HARD BLOCK
  os 的文件 I/O + 进程能力由 Layer A 覆盖（import os → 根因命中）。
  文件级检测（天然有限集）：
    BINARY_FILE (.so/.pyd/.dll/.dylib/.bundle)
    SYMLINK_ESCAPE（路径越界）
    SYNTAX_ERROR（AST 解析失败）

扫描范围：从 factory 模块出发，沿 import 传递闭包追溯——仅扫描会被
子进程实际加载的代码。不在闭包链上的文件不可能被执行，不产生能力。
对标 mypy --follow-imports=normal、Rust dead code elimination。
"""

from __future__ import annotations

import ast
import os as _os
from dataclasses import dataclass
from importlib.metadata import distribution
from importlib.util import find_spec as _find_spec
from pathlib import Path

from astrobase import LogfmtLogger

log = LogfmtLogger("astroframe.scanner")

# ── Layer A: 因果根模块 ────────────────────────────────────────────────────────
# 每个条目的模块名是其在 Python 标准库 + 整个生态中的唯一因果根。
# 不存在 socket 之外的网络库、subprocess 之外的进程创建机制、
# pickle/marshal 之外的反序列化入口、ctypes/cffi 之外的纯 Python FFI 门。
# 此集合无需随 Python 版本维护——新库的网络能力必然途经 socket。

_ROOT_CAUSE_MODULES: dict[str, tuple[tuple[str | None, bool, bool], ...]] = {
    # module_name: ((required_permission, is_hard_block, requires_signature), ...)
    #
    # 每个条目是其在 Python 中的唯一因果根——不存在 socket 之外的网络库、
    # subprocess/os.system 之外的进程创建、pickle/marshal 之外的反序列化入口、
    # ctypes/cffi 之外的纯 Python FFI 门。
    # os 提供文件 I/O + os.system/popen——import os = 拥有这些能力。
    # 此集合是封闭的——Python 生态不会产生新的因果根类别。
    "socket": (("network.outbound", False, False),),
    "os": (("filesystem.read", False, False), ("filesystem.write", False, False)),
    "subprocess": (("process.spawn", True, True),),
    "pickle": (("code.unpickle", True, False),),
    "marshal": (("code.unpickle", True, False),),
    "ctypes": (("code.ffi", True, True),),
    "cffi": (("code.ffi", True, True),),
}

# ── Layer B: dangerous builtins (无 import 可追溯的语言原语) ─────────────────────

_DANGEROUS_BUILTINS: dict[str, tuple[str | None, bool, bool]] = {
    "eval": ("code.dynamic", True, True),
    "exec": ("code.dynamic", True, True),
    "compile": ("code.dynamic", True, True),
    "open": ("filesystem.write", False, False),
    "breakpoint": (None, True, False),  # 沙箱内无调试器——无条件硬阻断
}

# ── os 模块进程调用——非模块主目的，需 AST 显式匹配 ──────────────────────────

_OS_PROCESS_CALLS: frozenset[str] = frozenset({"system", "popen", "execv", "execve", "spawnv", "spawnve"})

# ── 二进制扩展名 ────────────────────────────────────────────────────────────────

_BINARY_EXTENSIONS: frozenset[str] = frozenset({".so", ".pyd", ".dll", ".dylib", ".bundle"})

# ── 模块级缓存 ──────────────────────────────────────────────────────────────────

_scan_cache: dict[str, ScanResult] = {}
_stdlib_ast_cache: dict[str, ast.AST | None] = {}  # module_name → parsed AST (None = not found)
_closure_cache: dict[str, frozenset[tuple[str, str | None, bool, bool]]] = {}  # module_name → root causes


# ── 数据类型 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScanViolation:
    """单条扫描违规。

    违规消解遵循两维独立判定模型：
      required_permission  = None  → 此维度不适用（如 pickle.loads 不可声明权限）
      required_permission != None → 须在 manifest 中声明此权限
      requires_signature_verification = False → 不要求签名
      requires_signature_verification = True  → 须通过签名验证或信任记录
    """

    file_path: str
    line_number: int
    code: str
    message: str
    required_permission: str | None
    is_hard_block: bool
    requires_signature_verification: bool


@dataclass(frozen=True)
class ScanResult:
    """扫描结果——纯数据，不携带"已审核"状态。"""

    violations: tuple[ScanViolation, ...] = ()
    scanned_files: tuple[str, ...] = ()
    transitive_graph: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        return len(self.violations) == 0

    @property
    def hard_blocks(self) -> tuple[ScanViolation, ...]:
        return tuple(v for v in self.violations if v.is_hard_block)

    @property
    def requires_signature(self) -> bool:
        return any(v.requires_signature_verification for v in self.violations)

    @property
    def required_permissions(self) -> frozenset[str]:
        return frozenset(v.required_permission for v in self.violations if v.required_permission is not None)


# ── 公开 API ────────────────────────────────────────────────────────────────────


def scan_plugin_package(package_name: str, factory: str) -> ScanResult:
    """主入口——从 factory 模块出发，沿 import 链闭包追溯扫描。

    扫描范围 = factory 模块自身的 import 传递闭包。
    不在闭包链上的代码不可能被子进程加载——报告其 import 为能力是误报。

    对标: mypy --follow-imports=normal（只检查被实际导入的模块）、
          Go vet（整个 package 但 Go 的 import 语义不同——编译时全量链接）、
          Rust 的 dead code warning（未使用的 fn 不参与 borrow check）。

    cache key = f"{package_name}:{factory}"——不同 capability 的闭包不同。
    """
    cache_key = f"{package_name}:{factory}"
    cached = _scan_cache.get(cache_key)
    if cached is not None:
        return cached

    factory_module = factory.split(":")[0]
    pkg_top = factory_module.split(".")[0]

    try:
        pkg_root_raw = distribution(package_name).locate_file("")
    except Exception:
        log.debug("plugin_scanner_pkg_not_found", package=package_name)
        return ScanResult()

    if pkg_root_raw is None:
        return ScanResult()

    pkg_root = Path(str(pkg_root_raw)).resolve()
    if not pkg_root.is_dir():
        return ScanResult()

    pkg_dir = _find_python_package_dir(pkg_root, pkg_top)

    # BFS from factory module through internal imports
    factory_file = _resolve_module_to_file(factory_module, pkg_dir, pkg_top)
    if factory_file is None:
        factory_file = _find_module_in_dir(factory_module, pkg_dir, pkg_top)
    if factory_file is None:
        return ScanResult()

    violations: list[ScanViolation] = []
    scanned: list[str] = []
    module_names: list[str] = []
    visited: set[str] = set()
    queue: list[Path] = []

    # __init__.py of all ancestor packages are implicitly loaded by Python
    # when importing any submodule — include them in the closure
    root_init = pkg_dir / "__init__.py"
    if root_init.is_file():
        queue.append(root_init)
    for init_file in _collect_ancestor_init_files(factory_module, pkg_dir, pkg_top):
        queue.append(init_file)
    queue.append(factory_file)

    while queue:
        py_file = queue.pop(0)
        abs_path = str(py_file.absolute())
        if abs_path in visited:
            continue
        visited.add(abs_path)

        _scan_single_py(py_file, violations)
        scanned.append(abs_path)

        rel_path = py_file.relative_to(pkg_dir)
        parts = list(rel_path.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][:-3]
        module_name = ".".join([pkg_top] + parts) if parts else pkg_top
        module_names.append(module_name)

        # Enqueue internal imports
        for imported_module in _collect_plugin_imports(py_file, module_name):
            next_file = _resolve_module_to_file(imported_module, pkg_dir, pkg_top)
            if next_file is None:
                next_file = _find_module_in_dir(imported_module, pkg_dir, pkg_top)
            if next_file is not None and str(next_file.absolute()) not in visited:
                queue.append(next_file)

    _scan_binaries_in_tree(pkg_dir, pkg_dir, violations)

    result = ScanResult(
        violations=tuple(violations),
        scanned_files=tuple(scanned),
        transitive_graph=tuple(module_names),
    )

    if scanned:
        _scan_cache[cache_key] = result
    return result


def scan_py_file(file_path: Path) -> list[ScanViolation]:
    """单文件 AST 扫描——供测试和递归使用。"""
    violations: list[ScanViolation] = []
    _scan_single_py(file_path, violations)
    return violations


# ── 包目录解析 ──────────────────────────────────────────────────────────────────


def _find_python_package_dir(pkg_root: Path, pkg_top: str) -> Path:
    """定位 Python 包目录。"""
    if pkg_root.name == pkg_top and (pkg_root / "__init__.py").is_file():
        return pkg_root

    candidate = pkg_root / pkg_top
    if candidate.is_dir() and (candidate / "__init__.py").is_file():
        return candidate

    candidate = pkg_root / "src" / pkg_top
    if candidate.is_dir() and (candidate / "__init__.py").is_file():
        return candidate

    for init_file in pkg_root.rglob("__init__.py"):
        parent = init_file.parent
        if parent.name == pkg_top:
            return parent

    return pkg_root


# ── 二进制文件检测 ──────────────────────────────────────────────────────────────


def _scan_binaries_in_tree(
    root_dir: Path,
    current_dir: Path,
    violations: list[ScanViolation],
) -> None:
    """递归遍历包目录，检测二进制文件 + 符号链接逃逸。"""
    try:
        entries = list(_os.scandir(current_dir))
    except OSError as exc:
        log.warning("plugin_scanner_scandir_error", path=str(current_dir), error=str(exc))
        return

    for entry in entries:
        if entry.is_symlink():
            try:
                resolved = Path(entry.path).resolve()
                if not str(resolved).startswith(str(root_dir)):
                    violations.append(
                        ScanViolation(
                            file_path=str(Path(entry.path).absolute()),
                            line_number=0,
                            code="SYMLINK_ESCAPE",
                            message=f"{entry.path} → {resolved}: 符号链接指向包目录外",
                            required_permission=None,
                            is_hard_block=True,
                            requires_signature_verification=False,
                        )
                    )
                    continue
            except OSError:
                pass

        if entry.is_dir(follow_symlinks=False):
            _scan_binaries_in_tree(root_dir, Path(entry.path), violations)
            continue

        if not entry.is_file(follow_symlinks=False):
            continue

        ext = Path(entry.path).suffix.lower()
        if ext in _BINARY_EXTENSIONS:
            violations.append(
                ScanViolation(
                    file_path=str(Path(entry.path).absolute()),
                    line_number=0,
                    code="BINARY_FILE",
                    message=f"{entry.name}: 包内二进制文件 ({ext}) 需要签名验证",
                    required_permission=None,
                    is_hard_block=True,
                    requires_signature_verification=True,
                )
            )


# ── 单文件 AST 扫描 ─────────────────────────────────────────────────────────────


def _scan_single_py(
    file_path: Path,
    violations: list[ScanViolation],
) -> list[ScanViolation]:
    """AST 解析单个 .py 文件，检测所有违规类型。"""
    file_violations: list[ScanViolation] = []
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.warning("plugin_scanner_read_error", path=str(file_path), error=str(exc))
        return file_violations

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        lineno = exc.lineno or 0
        violations.append(
            ScanViolation(
                file_path=str(file_path.absolute()),
                line_number=lineno,
                code="SYNTAX_ERROR",
                message=f"{file_path}:{lineno} — 语法错误: {exc.msg}",
                required_permission=None,
                is_hard_block=True,
                requires_signature_verification=False,
            )
        )
        return file_violations

    visitor = _SecurityVisitor(str(file_path.absolute()), file_violations)
    visitor.visit(tree)

    violations.extend(file_violations)
    return file_violations


# ── Layer A: import 闭包追溯 ────────────────────────────────────────────────────


def _resolve_module_file(module_name: str) -> Path | None:
    """定位模块的 .py 文件路径，不执行 import。"""
    try:
        spec = _find_spec(module_name)
    except (ValueError, ImportError, ModuleNotFoundError):
        return None
    if spec is None or spec.origin is None:
        return None
    origin = Path(spec.origin)
    if origin.is_file() and origin.suffix == ".py":
        return origin
    return None


def _parse_module_ast(module_name: str) -> ast.AST | None:
    """解析模块 AST，带缓存。对所有能找到 .py 源文件的模块递归。

    停止条件天然由 _resolve_module_file 提供——无 .py 源码的模块
    （C 扩展、namespace package 等）返回 None，递归在此终止。
    """
    if module_name in _stdlib_ast_cache:
        return _stdlib_ast_cache[module_name]

    mod_file = _resolve_module_file(module_name)
    if mod_file is None:
        _stdlib_ast_cache[module_name] = None
        return None

    try:
        source = mod_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(mod_file))
        _stdlib_ast_cache[module_name] = tree
        return tree
    except (OSError, UnicodeDecodeError, SyntaxError):
        _stdlib_ast_cache[module_name] = None
        return None


def _trace_import_closure_root_causes(
    module_name: str,
    visited: frozenset[str] | None = None,
) -> frozenset[tuple[str, str | None, bool, bool]]:
    """递归追溯模块的 import 闭包至因果根。

    策略：
      1. 模块自身是因果根 → 直接返回
      2. 模块是 stdlib → 展开其全部 imports（含函数体），递归追踪
      3. 模块非 stdlib 或无源码 → 停止

    全量遍历——import 的位置无关。只要存在一条代码路径能让
    目标模块进入 sys.modules，能力即存在。相对导入通过 parent_module 解析。
    """
    if visited is None:
        visited = frozenset()
    if module_name in visited:
        return frozenset()
    visited = visited | {module_name}

    if module_name in _closure_cache:
        return _closure_cache[module_name]

    if module_name in _ROOT_CAUSE_MODULES:
        entries = _ROOT_CAUSE_MODULES[module_name]
        result: frozenset[tuple[str, str | None, bool, bool]] = frozenset(
            (module_name, perm, hard, sig) for perm, hard, sig in entries
        )
        _closure_cache[module_name] = result
        return result

    tree = _parse_module_ast(module_name)
    if tree is None:
        _closure_cache[module_name] = frozenset()
        return frozenset()

    collector = _ImportCollector(parent_module=module_name)
    collector.visit(tree)

    all_root_causes: set[tuple[str, str | None, bool, bool]] = set()
    for imported in collector.imports:
        if imported in _ROOT_CAUSE_MODULES:
            for perm, hard, sig in _ROOT_CAUSE_MODULES[imported]:
                all_root_causes.add((imported, perm, hard, sig))
        else:
            causes = _trace_import_closure_root_causes(imported, visited)
            all_root_causes.update(causes)

    result = frozenset(all_root_causes)
    _closure_cache[module_name] = result
    return result


class _ImportCollector(ast.NodeVisitor):
    """收集模块的所有 import 语句（全量遍历，含函数/类体 + 动态导入）。

    相对导入通过 parent_module 解析为绝对模块名。
    动态导入 __import__("X") / importlib.import_module("X") 仅收集常量参数。
    用于根因追溯和闭包 BFS 两个场景——收集所有可静态解析的导入目标。
    """

    def __init__(self, parent_module: str = "") -> None:
        self.imports: list[str] = []
        self._parent = parent_module

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0:
            if node.module is not None:
                self.imports.append(node.module)
        else:
            mod = _resolve_relative_import(self._parent, node.module, node.level)
            if mod is not None:
                self.imports.append(mod)

    def visit_Call(self, node: ast.Call) -> None:
        if _is_dynamic_import_call(node):
            arg = _extract_first_arg(node, "name")
            if arg is not None and isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                self.imports.append(arg.value)
        self.generic_visit(node)


# ── AST 安全访问器 ──────────────────────────────────────────────────────────────


class _SecurityVisitor(ast.NodeVisitor):
    """AST 安全访问器——检测所有 S5 违规类型。

    Layer A: import 语句 → 逐链追溯至因果根
    Layer B: 调用表达式 → 检测无 import 可追溯的语言原语（builtin / os.xxx）
    """

    def __init__(self, file_path: str, violations: list[ScanViolation]) -> None:
        self._file = file_path
        self._violations = violations
        self._seen: set[tuple[str, str | None, bool, bool]] = set()

    def _add_violation(
        self,
        lineno: int,
        code: str,
        message: str,
        required_permission: str | None,
        is_hard_block: bool,
        requires_sig: bool,
    ) -> None:
        key = (code, required_permission, is_hard_block, requires_sig)
        if key in self._seen:
            return
        self._seen.add(key)
        self._violations.append(
            ScanViolation(
                file_path=self._file,
                line_number=lineno,
                code=code,
                message=f"{self._file}:{lineno} — {message}",
                required_permission=required_permission,
                is_hard_block=is_hard_block,
                requires_signature_verification=requires_sig,
            )
        )

    # ── Layer A: Import / ImportFrom → 闭包追溯 ─────────────────────────────

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_import_closure(alias.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is not None:
            self._check_import_closure(node.module, node.lineno)
        self.generic_visit(node)

    def _check_import_closure(self, module_name: str, lineno: int) -> None:
        """追溯模块的 import 闭包，标记所有命中的因果根违规。"""
        root_causes = _trace_import_closure_root_causes(module_name)
        for cause_name, permission, is_hard, requires_sig in root_causes:
            self._add_violation(
                lineno,
                "ROOT_CAUSE_IMPORT",
                f"import {module_name} → 追溯至因果根 {cause_name}，需要 {permission} 权限",
                required_permission=permission,
                is_hard_block=is_hard,
                requires_sig=requires_sig,
            )

    # ── Layer B: Call 表达式 → builtin / os.xxx ────────────────────────────

    def visit_Call(self, node: ast.Call) -> None:
        # builtin: eval / exec / compile / open / breakpoint
        if isinstance(node.func, ast.Name):
            name = node.func.id
            if name in _DANGEROUS_BUILTINS:
                perm, is_hard, requires_sig = _DANGEROUS_BUILTINS[name]
                self._add_violation(
                    node.lineno,
                    "DANGEROUS_BUILTIN",
                    f"{name}() 需要 {perm} 权限" if perm else f"{name}() 无条件硬阻断",
                    required_permission=perm,
                    is_hard_block=is_hard,
                    requires_sig=requires_sig,
                )

        # os.system / os.popen / os.execv → process.spawn
        # os 是通用 OS 接口模块——仅特定函数调用意味着进程创建能力
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in _OS_PROCESS_CALLS:
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                    self._add_violation(
                        node.lineno,
                        "OS_PROCESS_CALL",
                        f"os.{attr}() 需要 process.spawn 权限 + 签名验证",
                        required_permission="process.spawn",
                        is_hard_block=True,
                        requires_sig=True,
                    )

        # pickle.loads / marshal.loads —— 无条件硬阻断
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in ("loads", "load"):
                if isinstance(node.func.value, ast.Name):
                    mod = node.func.value.id
                    if mod in ("pickle", "marshal"):
                        self._add_violation(
                            node.lineno,
                            "PICKLE_LOAD",
                            f"{mod}.{attr}() 无条件硬阻断",
                            required_permission=None,
                            is_hard_block=True,
                            requires_sig=False,
                        )

        # __import__ / importlib.import_module → 动态导入检查
        if isinstance(node.func, ast.Name) and node.func.id == "__import__":
            self._check_dynamic_import(node, node.lineno)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "import_module":
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "importlib":
                self._check_dynamic_import(node, node.lineno)

        self.generic_visit(node)

    def _check_dynamic_import(self, node: ast.Call, lineno: int) -> None:
        """检测 __import__/importlib.import_module 的参数是否为常量。

        常量字符串 → 可静态分析 → 沿 import 闭包追溯至因果根。
        非常量参数 → code.dynamic ——不可静态分析。
        """
        arg = _extract_first_arg(node, "name")
        if arg is None:
            return

        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            module_name = arg.value
            # 常量参数 → 可静态分析 → 走 import 闭包追溯
            root_causes = _trace_import_closure_root_causes(module_name)
            for cause_name, permission, is_hard, requires_sig in root_causes:
                self._add_violation(
                    lineno,
                    "ROOT_CAUSE_IMPORT",
                    f'动态导入 "{module_name}" → 追溯至因果根 {cause_name}，需要 {permission} 权限',
                    required_permission=permission,
                    is_hard_block=is_hard,
                    requires_sig=requires_sig,
                )
            return

        # 非常量参数 → 无法静态分析 → code.dynamic
        self._add_violation(
            lineno,
            "DYNAMIC_IMPORT",
            "非常量参数的动态导入需要 code.dynamic 权限 + 签名验证",
            required_permission="code.dynamic",
            is_hard_block=True,
            requires_sig=True,
        )


def _extract_first_arg(node: ast.Call, param_name: str = "name") -> ast.expr | None:
    """从 Call 节点提取第一个参数（优先位置参数，其次关键字参数）。"""
    if node.args:
        return node.args[0]
    for kw in node.keywords:
        if kw.arg == param_name:
            return kw.value
    return None


# ── 缓存清理 ────────────────────────────────────────────────────────────────────


def _collect_plugin_imports(file_path: Path, module_name: str) -> list[str]:
    """收集文件中指向插件自身的所有导入——用于闭包 BFS。

    委托 _ImportCollector 收集全部可静态解析的导入目标，
    过滤出插件内部模块名（以 pkg_top 开头或可通过 find_spec 定位到包内文件）。
    """
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []

    collector = _ImportCollector(parent_module=module_name)
    collector.visit(tree)
    return collector.imports


def _is_dynamic_import_call(node: ast.Call) -> bool:
    """是否为动态导入调用——覆盖全部写法。

    __import__("X")
    importlib.import_module("X")
    from importlib import import_module; import_module("X")
    builtins.__import__("X")
    """
    if isinstance(node.func, ast.Name):
        return node.func.id in ("__import__", "import_module")
    if isinstance(node.func, ast.Attribute):
        return (
            node.func.attr in ("__import__", "import_module")
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in ("importlib", "builtins")
        )
    return False


def _resolve_relative_import(parent_module: str, module: str | None, level: int) -> str | None:
    """将相对导入解析为绝对模块名。

    Python 语义：相对导入起点是 __package__。
    包（__init__.py）→ __package__ == __name__
    模块（foo.py）→ __package__ == 父包名
    """
    if not parent_module:
        return None
    if _is_package(parent_module):
        pkg = parent_module
    else:
        parts = parent_module.rsplit(".", 1)
        pkg = parts[0] if len(parts) == 2 else parent_module

    if level == 1:
        base = pkg
    else:
        parts = pkg.rsplit(".", level - 1)
        if len(parts) < level:
            return None
        base = parts[0]

    if module is not None:
        return f"{base}.{module}" if base else module
    return base


def _resolve_module_to_file(module_name: str, pkg_dir: Path, pkg_top: str) -> Path | None:
    """将模块名解析为包目录内的 .py 文件路径（仅插件内部模块）。"""
    if not module_name.startswith(pkg_top + ".") and module_name != pkg_top:
        return None
    rel = module_name[len(pkg_top) + 1 :] if module_name != pkg_top else ""
    parts = rel.split(".") if rel else []
    candidate_pkg = pkg_dir.joinpath(*parts) / "__init__.py"
    if candidate_pkg.is_file():
        return candidate_pkg
    candidate_mod = pkg_dir.joinpath(*parts[:-1], parts[-1] + ".py") if parts else None
    if candidate_mod and candidate_mod.is_file():
        return candidate_mod
    return None


def _find_module_in_dir(module_name: str, pkg_dir: Path, pkg_top: str) -> Path | None:
    """在包目录中搜索模块文件（回退——处理非标准布局）。"""
    rel = module_name[len(pkg_top) + 1 :] if module_name.startswith(pkg_top + ".") else module_name
    parts = rel.split(".")
    for init_file in pkg_dir.rglob("__init__.py"):
        parent = init_file.parent
        if parent.relative_to(pkg_dir).as_posix().replace("/", ".") == rel:
            return init_file
    for py_file in pkg_dir.rglob("*.py"):
        if py_file.stem == parts[-1]:
            return py_file
    return None


def _collect_ancestor_init_files(factory_module: str, pkg_dir: Path, pkg_top: str) -> list[Path]:
    """收集 factory 模块的所有祖先包的 __init__.py。

    Python 导入 perftest.mod_000 时必然先执行 perftest/__init__.py，
    即使 import 语句中没有显式引用它。这些文件是闭包的一部分。
    """
    if factory_module == pkg_top:
        return []
    parts = factory_module[len(pkg_top) + 1 :].split(".") if factory_module != pkg_top else []
    result: list[Path] = []
    current = [pkg_top]
    for part in parts[:-1]:  # all ancestors except the module itself
        current.append(part)
        init = pkg_dir.joinpath(*current[1:]) / "__init__.py"
        if init.is_file():
            result.append(init)
    return result


def _is_package(module_name: str) -> bool:
    """检查模块名是否为包（含 PEP 420 namespace package）而非单文件模块。"""
    try:
        spec = _find_spec(module_name)
    except (ValueError, ImportError, ModuleNotFoundError):
        return False
    if spec is None:
        return False
    if spec.origin is not None:
        return Path(spec.origin).name == "__init__.py"
    # PEP 420 namespace package — no __init__.py, has submodule_search_locations
    return bool(spec.submodule_search_locations)


def _clear_cache() -> None:
    """清除所有缓存（仅供测试使用）。"""
    _scan_cache.clear()
    _stdlib_ast_cache.clear()
    _closure_cache.clear()
