"""插件系统共享类型 — Protocol、dataclass、enum、GROUP_PROTOCOL 映射、权限目录、审计常量。

纯基础设施，零业务依赖。对标 pluggy (hook spec/impl) + Dagster (Protocol 类型契约) + Kubernetes (apiVersion 逻辑名)。
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from astrobase._types import AsyncCloseable

if TYPE_CHECKING:
    from pathlib import Path

# ── Hook 调度原语 ──────────────────────────────────────────────────────────────


class HookKind(str, Enum):
    """底座提供的两种 hook 调度模式（ADR-0011 决策 3）。

    COLLECTOR: 收集所有实现，返回按 capability 唯一键索引的注册表 dict[str, T]
    CHAIN:     按序执行，输出传给下一级，is_terminal 停止
    """

    COLLECTOR = "collector"
    CHAIN = "chain"


# ── 插件状态 ───────────────────────────────────────────────────────────────────


class PluginStatus(str, Enum):
    """插件在注册表中的运行时状态（ADR-0011 决策 7）。

    LOADED:         正常加载，capability 正常注册
    PENDING_REVIEW: 首次发现需用户确认，capability 不注册
    DISABLED:       用户禁用，零 import
    FAILED:         import 抛异常或 isinstance() 不通过
    SANDBOX_CRASH:  子进程意外退出，自动重启 ≤ 3 次
    INCOMPATIBLE:   requires_engine 不满足或依赖不可用
    """

    LOADED = "loaded"
    PENDING_REVIEW = "pending_review"
    DISABLED = "disabled"
    FAILED = "failed"
    SANDBOX_CRASH = "sandbox_crash"
    INCOMPATIBLE = "incompatible"


# ── 权限等级 ───────────────────────────────────────────────────────────────────


class PermissionLevel(str, Enum):
    """capability 的权限等级，由 manifest 加载时从 permissions 列表计算（ADR-0011 决策 14）。

    NORMAL:    仅含普通权限（filesystem.*, crawl.ctx.read, crawl.deps.read）
    DANGEROUS: 含 ⚠️ 权限（crawl.ctx.write, network.outbound）
    SIGNATURE: 含 🔴 权限（crawl.deps.db, crawl.deps.queue, process.spawn, code.dynamic）
    """

    NORMAL = "normal"
    DANGEROUS = "dangerous"
    SIGNATURE = "signature"


# ── 插件 Tier — 引擎对 group 类型的官方分类标签 ──────────────────────────


class Tier(str, Enum):
    """引擎对 group 类型的官方分类标签（ADR-0011 S4）。

    由引擎开发者按 group 分配，第三方不可声明或修改。register_group() 统一设为 SERVICE。
    PermissionLevel 负责运行时路由和信任决策；Tier 是纯展示元数据——
    CLI plugins list 显示、未来 AI agent 分类，不参与任何安全或执行决策。

    CORE_WARNING / CORE_SIGNATURE：仅引擎内置核心管线 group 使用。
    CORE_SIGNATURE 当前无线下使用者——🔴 权限的判定由 PermissionLevel.SIGNATURE 完成。
    """

    SERVICE = "service"
    CORE_WARNING = "core_warning"
    CORE_SIGNATURE = "core_signature"
    UI = "ui"


# ── ExecutionMode — 工厂创建实例的路由决策 ─────────────────────────────────────


class ExecutionMode(str, Enum):
    """插件实例的执行模式，由 create_plugin_instance() 根据 PermissionLevel 路由。

    所有插件统一在子进程中运行（ADR-0011 决策 14 —— Chrome/Deno/iOS 统一沙箱原则）。
    SUBPROCESS_SIGNED 仅在 SIGNATURE 权限时附加签名验证。
    """

    SUBPROCESS = "subprocess"
    SUBPROCESS_SIGNED = "subprocess_signed"


# ── DeprecationSeverity — capability 废弃窗口三阶段 ────────────────────────────


class DeprecationSeverity(str, Enum):
    """capability 废弃的严重等级（ADR-0011 S3 + 废弃策略）。

    NONE:    未废弃，或引擎版本早于 deprecated_since
    WARNING:  废弃公告的 2 个 minor version 宽限期内——功能正常，WARNING 日志
    ERROR:    宽限期结束后 1 个 minor version——功能可用但需显式 opt-in
    REMOVED:  capability 不再注册，引用者收到 CapabilityNotFoundError
    """

    NONE = "none"
    WARNING = "warning"
    ERROR = "error"
    REMOVED = "removed"


# PEP 440 版本号解析，用于 minor version 比较
_VERSION_PARSE = _re.compile(r"^([0-9]+)(?:\.([0-9]+))?(?:\.([0-9]+))?")


def _parse_version_tuple(version: str) -> tuple[int, int, int]:
    """解析 PEP 440 版本字符串为 (major, minor, patch)。非法格式返回 (0, 0, 0)。"""
    m = _VERSION_PARSE.match(version.strip())
    if not m:
        return (0, 0, 0)
    parts = m.groups()
    return (
        int(parts[0]) if parts[0] else 0,
        int(parts[1]) if parts[1] else 0,
        int(parts[2]) if parts[2] else 0,
    )


def check_deprecation(deprecated_since: str | None, engine_version: str) -> DeprecationSeverity:
    """根据废弃窗口计算 capability 的当前阶段。

    对标 Kubernetes deprecation policy:
      deprecated_since 之后 2 个 minor version → WARNING → 1 个 → ERROR → 之后 → REMOVED。

    deprecated_since = "0.2.0", engine = "0.4.0":
      minor_delta = 4 - 2 = 2 → ERROR 阶段（0.2/0.3 宽限期已过，0.4 需显式 opt-in）

    deprecated_since = None → 未废弃。
    major version 不同 → 保守 WARNING（重大版本变更不自动删除）。
    """
    if deprecated_since is None:
        return DeprecationSeverity.NONE

    dep = _parse_version_tuple(deprecated_since)
    eng = _parse_version_tuple(engine_version)

    # "0.0.0" / 无法解析的版本号 → 永久 WARNING（不自动升级到 ERROR/REMOVED）
    is_indefinite = dep == (0, 0, 0)
    if is_indefinite:
        return DeprecationSeverity.WARNING

    # 跨 major version —— 保守 WARNING，不自动升级到删除
    if dep[0] != eng[0]:
        return DeprecationSeverity.WARNING

    # 引擎版本早于废弃公告版本（含同 minor 内 patch 回退）→ 不触发
    if (eng[0], eng[1], eng[2]) < (dep[0], dep[1], dep[2]):
        return DeprecationSeverity.NONE

    minor_delta = eng[1] - dep[1]
    if minor_delta <= 1:
        return DeprecationSeverity.WARNING
    if minor_delta == 2:
        return DeprecationSeverity.ERROR
    return DeprecationSeverity.REMOVED


# ── 权限目录 ───────────────────────────────────────────────────────────────────

# 能力权限 — 授予操作能力
_PERMISSION_CRAWL_CTX_READ = "crawl.ctx.read"
_PERMISSION_CRAWL_CTX_WRITE = "crawl.ctx.write"
_PERMISSION_CRAWL_DEPS_READ = "crawl.deps.read"
_PERMISSION_CRAWL_DEPS_DB = "crawl.deps.db"
_PERMISSION_CRAWL_DEPS_QUEUE = "crawl.deps.queue"
_PERMISSION_NETWORK_OUTBOUND = "network.outbound"
_PERMISSION_FILESYSTEM_OUTPUT = "filesystem.output"
_PERMISSION_FILESYSTEM_TEMP = "filesystem.temp"
_PERMISSION_FILESYSTEM_STATE = "filesystem.state"
_PERMISSION_FILESYSTEM_READ = "filesystem.read"
_PERMISSION_FILESYSTEM_WRITE = "filesystem.write"
_PERMISSION_PROCESS_SPAWN = "process.spawn"
_PERMISSION_CODE_DYNAMIC = "code.dynamic"
_PERMISSION_CODE_FFI = "code.ffi"
_PERMISSION_CODE_UNPICKLE = "code.unpickle"

# 约束权限 — 修饰能力权限，不授予独立操作能力
_PERMISSION_NETWORK_DOMAINS = "network.domains"

# 能力权限集合 — 每项授予独立操作能力，参与 PermissionLevel 推导
_CAPABILITY_PERMISSIONS: frozenset[str] = frozenset(
    {
        _PERMISSION_CRAWL_CTX_READ,
        _PERMISSION_CRAWL_CTX_WRITE,
        _PERMISSION_CRAWL_DEPS_READ,
        _PERMISSION_CRAWL_DEPS_DB,
        _PERMISSION_CRAWL_DEPS_QUEUE,
        _PERMISSION_NETWORK_OUTBOUND,
        _PERMISSION_FILESYSTEM_OUTPUT,
        _PERMISSION_FILESYSTEM_TEMP,
        _PERMISSION_FILESYSTEM_STATE,
        _PERMISSION_FILESYSTEM_READ,
        _PERMISSION_FILESYSTEM_WRITE,
        _PERMISSION_PROCESS_SPAWN,
        _PERMISSION_CODE_DYNAMIC,
        _PERMISSION_CODE_FFI,
        _PERMISSION_CODE_UNPICKLE,
    }
)

# 约束权限集合 — 修饰能力权限，不授予独立操作能力，不参与 PermissionLevel 推导
_CONSTRAINT_PERMISSIONS: frozenset[str] = frozenset({_PERMISSION_NETWORK_DOMAINS})

# 所有已知权限名的并集
_ALL_KNOWN_PERMISSIONS: frozenset[str] = _CAPABILITY_PERMISSIONS | _CONSTRAINT_PERMISSIONS

# 按等级分类的能力权限
_NORMAL_PERMISSIONS: frozenset[str] = frozenset(
    {
        _PERMISSION_CRAWL_CTX_READ,
        _PERMISSION_CRAWL_DEPS_READ,
        _PERMISSION_FILESYSTEM_OUTPUT,
        _PERMISSION_FILESYSTEM_TEMP,
        _PERMISSION_FILESYSTEM_STATE,
    }
)

_DANGEROUS_PERMISSIONS: frozenset[str] = frozenset(
    {
        _PERMISSION_CRAWL_CTX_WRITE,
        _PERMISSION_NETWORK_OUTBOUND,
        _PERMISSION_FILESYSTEM_READ,
        _PERMISSION_FILESYSTEM_WRITE,
    }
)

_SIGNATURE_PERMISSIONS: frozenset[str] = frozenset(
    {
        _PERMISSION_CRAWL_DEPS_DB,
        _PERMISSION_CRAWL_DEPS_QUEUE,
        _PERMISSION_PROCESS_SPAWN,
        _PERMISSION_CODE_DYNAMIC,
        _PERMISSION_CODE_FFI,
        _PERMISSION_CODE_UNPICKLE,
    }
)

# crawl.ctx.write 隐含 read, filesystem.write 隐含 read
_WRITE_IMPLIES_READ: dict[str, str] = {
    _PERMISSION_CRAWL_CTX_WRITE: _PERMISSION_CRAWL_CTX_READ,
    _PERMISSION_FILESYSTEM_WRITE: _PERMISSION_FILESYSTEM_READ,
}

# 默认授予的权限
_DEFAULT_GRANTED_PERMISSIONS: frozenset[str] = frozenset({_PERMISSION_FILESYSTEM_STATE})


# PermissionLevel 有序比较索引——str Enum 的 > 使用字典序（"dangerous" > "normal" = False），
# 必须使用显式数值索引进行比较
_PERMISSION_LEVEL_ORDER: dict[PermissionLevel, int] = {
    PermissionLevel.NORMAL: 0,
    PermissionLevel.DANGEROUS: 1,
    PermissionLevel.SIGNATURE: 2,
}


def permission_level_higher_than(a: PermissionLevel, b: PermissionLevel) -> bool:
    """比较 a 的权限等级是否严格高于 b（使用数值索引而非 str 字典序）。"""
    return _PERMISSION_LEVEL_ORDER.get(a, -1) > _PERMISSION_LEVEL_ORDER.get(b, -1)


def derive_permission_level(permissions: list[str]) -> PermissionLevel:
    """从权限列表计算 PermissionLevel。约束权限不参与推导。"""
    capability_perms = [p for p in permissions if p in _CAPABILITY_PERMISSIONS]
    if any(p in _SIGNATURE_PERMISSIONS for p in capability_perms):
        return PermissionLevel.SIGNATURE
    if any(p in _DANGEROUS_PERMISSIONS for p in capability_perms):
        return PermissionLevel.DANGEROUS
    return PermissionLevel.NORMAL


def derive_execution_mode(level: PermissionLevel) -> ExecutionMode:
    """从 PermissionLevel 推导 ExecutionMode（ADR-0011 决策 14 统一沙箱模型）。

    所有权限等级统一在子进程中运行。SIGNATURE 附加签名验证。
    """
    if level == PermissionLevel.SIGNATURE:
        return ExecutionMode.SUBPROCESS_SIGNED
    return ExecutionMode.SUBPROCESS


# ── 审计事件常量 ───────────────────────────────────────────────────────────────


class AuditEvent:
    """审计事件名称常量（ADR-0011 S16 + Phase 2 扩展）。"""

    PERMISSION_GRANT = "plugin_permission_grant"
    PERMISSION_DENIED = "plugin_permission_denied"
    DATA_EGRESS = "plugin_data_egress"
    AUDIT_IMPORT = "plugin_audit_import"
    AUDIT_BLOCKED = "plugin_audit_blocked"
    SANDBOX_CRASH = "plugin_sandbox_crash"
    TRUST_PENDING = "plugin_trust_pending"
    SANDBOX_LAYER_ACTIVE = "plugin_sandbox_layer_active"
    SANDBOX_LAYER_FAILED = "plugin_sandbox_layer_failed"
    GLOBAL_UNDECLARED_PERMISSION = "plugin_global_undeclared_permission"
    STATE_RESTORE_FAILED = "plugin_state_restore_failed"
    SUBPROCESS_STDERR = "plugin_subprocess_stderr"


# ── PluginGlobal / PluginScoped 底座 Protocol ──────────────────────────────────


@runtime_checkable
class PluginGlobal(Protocol):
    """进程级单例生命周期。底座保证 on_load 调用一次，on_unload 调用一次。
    实现者自己保证线程安全——底座不提供锁。

    ADR-0011 决策 5：对标 Dagster global resource 生命周期桶。
    """

    @staticmethod
    def on_load() -> None: ...
    @staticmethod
    def on_unload() -> None: ...


@runtime_checkable
class PluginScoped(AsyncCloseable, Protocol):
    """有状态的可新建实例。底座提供工厂 create_instance()，消费者决定何时创建/销毁。

    底座只约定 setup() 和 aclose()（继承自 AsyncCloseable）两个生命周期方法。
    process() 由各子系统 Protocol 定义——不同子系统输入/输出类型不同。
    """

    async def setup(self, config: dict[str, Any]) -> None: ...


# ── 子系统 Protocol（12 个）─────────────────────────────────────────────────────


@runtime_checkable
class Processor(PluginScoped, Protocol):
    """爬虫链处理器——CHAIN 调度，按序执行。"""

    async def process(self, ctx: Any, deps: Any) -> Any: ...


@runtime_checkable
class Exporter(PluginScoped, Protocol):
    """输出导出器——COLLECTOR 调度。"""

    async def write(self, items: list[dict[str, Any]]) -> None: ...


@runtime_checkable
class ChatProvider(PluginScoped, Protocol):
    """AI Provider——COLLECTOR 调度，对标 ADR-0006 entry_points 模式。"""

    async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any: ...
    async def stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any: ...


@runtime_checkable
class HealthCheck(PluginScoped, Protocol):
    """健康检查——COLLECTOR 调度。"""

    async def check(self) -> dict[str, Any]: ...


@runtime_checkable
class Transform(PluginScoped, Protocol):
    """规则转换器——COLLECTOR 调度，纯函数语义。"""

    def transform(self, value: str, config: dict[str, Any]) -> str: ...


@runtime_checkable
class RuleSource(PluginScoped, Protocol):
    """规则源——COLLECTOR 调度，从外部获取规则。"""

    async def fetch(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class ContentExtractor(PluginScoped, Protocol):
    """内容提取器——COLLECTOR 调度。"""

    async def extract(self, html: str, url: str, config: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class CLISubcommand(PluginScoped, Protocol):
    """CLI 子命令——COLLECTOR 调度。"""

    def run(self, args: list[str]) -> int: ...


@runtime_checkable
class GUIPage(PluginScoped, Protocol):
    """GUI 页面——COLLECTOR 调度。Qt 硬约束：进程内模式。"""

    def create_widget(self, parent: Any) -> Any: ...
    def page_title(self) -> str: ...


@runtime_checkable
class BrowserHook(PluginScoped, Protocol):
    """浏览器事件钩子——COLLECTOR 调度。"""

    async def on_navigation_start(self, url: str, context: dict[str, Any]) -> None: ...
    async def on_navigation_end(self, url: str, context: dict[str, Any]) -> None: ...


@runtime_checkable
class URLFilter(PluginScoped, Protocol):
    """URL 过滤器——CHAIN 调度，纯函数语义。"""

    def filter(self, url: str, config: dict[str, Any]) -> bool: ...


@runtime_checkable
class LifecycleHook(PluginScoped, Protocol):
    """生命周期钩子——COLLECTOR 调度。观察爬虫/工作流生命周期事件。"""

    async def on_start(self, context: dict[str, Any]) -> None: ...
    async def on_stop(self, context: dict[str, Any]) -> None: ...
    async def on_error(self, error: Exception, context: dict[str, Any]) -> None: ...


# ── 签名验证器 Protocol（ADR-0011 S17）─────────────────────────────────────────


@runtime_checkable
class SignatureVerifier(Protocol):
    """插件包签名验证器——对标 ADR-0006 AI Provider entry_points + Protocol 模式。

    内置实现：sigstore（可选依赖 sigstore-python）、gpg（subprocess 调用 CLI）、unsigned（永远 UNVERIFIED）。
    verify() 结果是静态方法——验证器不持有状态。
    """

    @staticmethod
    def verify(dist_path: Path, manifest: PluginManifest, signing: dict[str, Any]) -> SignatureResult: ...


# ── register_group() — 第三方 group 注册 ────────────────────────────────────────


def register_group(group: str, protocol_cls: type) -> None:
    """注册第三方能力类型（对标 Kubernetes CRD — CustomResourceDefinition）。

    引擎记录 group → None 映射。不持有 Protocol 类引用，不做 isinstance 校验。
    校验责任在声明该 group 的消费者。
    第三方 group 的 Tier 统一为 SERVICE，不可自定义——引擎控制所有 group 的官方分类。
    """
    if group in GROUP_PROTOCOL:
        raise ValueError(f"group '{group}' is already registered")
    GROUP_PROTOCOL[group] = None
    _GROUP_TIER[group] = Tier.SERVICE
    _VALID_IMPLEMENTS[group] = frozenset()


# ── GROUP → Protocol 映射（SSOT）──────────────────────────────────────────────


GROUP_PROTOCOL: dict[str, type | None] = {
    "processor.chain": Processor,
    "ai.provider": ChatProvider,
    "rules.transform": Transform,
    "health.check": HealthCheck,
    "rules.source": RuleSource,
    "content.extractor": ContentExtractor,
    "output.exporter": Exporter,
    "cli.subcommand": CLISubcommand,
    "gui.page": GUIPage,
    "browser.hook": BrowserHook,
    "url.filter": URLFilter,
    "lifecycle.hook": LifecycleHook,
}
"""每个 group 的合法 implements Protocol（ADR-0011 决策 2 两段式校验 SSOT）。

引擎内置 group 的值是 Protocol 类（非 None）——引擎做 isinstance() 运行时校验。
第三方 group（通过 register_group() 注册）值为 None——引擎不持有 Protocol 类引用。
"""

# 合法 implements 值集合（每 group 当前仅 1 个合法值，未来 1→N 是纯增量）
_VALID_IMPLEMENTS: dict[str, frozenset[str]] = {
    group: frozenset({proto.__name__}) if proto is not None else frozenset() for group, proto in GROUP_PROTOCOL.items()
}

# group → Tier 推导表
_GROUP_TIER: dict[str, Tier] = {
    "processor.chain": Tier.CORE_WARNING,
    "ai.provider": Tier.SERVICE,
    "rules.transform": Tier.SERVICE,
    "health.check": Tier.SERVICE,
    "rules.source": Tier.SERVICE,
    "content.extractor": Tier.SERVICE,
    "output.exporter": Tier.SERVICE,
    "cli.subcommand": Tier.SERVICE,
    "gui.page": Tier.UI,
    "browser.hook": Tier.CORE_WARNING,
    "url.filter": Tier.SERVICE,
    "lifecycle.hook": Tier.SERVICE,
}


# ── Protocol 方法映射（SSOT）─────────────────────────────────────────────────────


_PROTOCOL_METHOD: dict[str, tuple[str, ...]] = {
    "Processor": ("process",),
    "Exporter": ("write",),
    "ChatProvider": ("chat", "stream"),
    "HealthCheck": ("check",),
    "Transform": ("transform",),
    "RuleSource": ("fetch",),
    "ContentExtractor": ("extract",),
    "CLISubcommand": ("run",),
    "GUIPage": ("create_widget", "page_title"),
    "BrowserHook": ("on_navigation_start", "on_navigation_end"),
    "URLFilter": ("filter",),
    "LifecycleHook": ("on_start", "on_stop", "on_error"),
}
"""每个 implements Protocol 逻辑名的合法方法名集合（ADR-0011 决策 2 两段式校验 SSOT）。

值为 tuple 而非单字符串——多方法 Protocol 允许多个合法方法名。
消费者（CLI、engine 等）通过此映射获知各 Protocol 支持的方法名。
host 不做方法名校验——父进程可信，host 仅做 PluginScoped 契约校验。
"""

# ── SandboxContext 子类注册表（Phase 2+ 预留数据通道）───────────────────────────

_SANDBOX_CONTEXT_REGISTRY: dict[str, type] = {}
"""SandboxContext 子类注册表——host 反序列化时的唯一解析源。

子系统通过 register_sandbox_context() 注册自己的 SandboxContext 子类。
host 仅从注册表查找——未注册的类名在反序列化时抛出 ValueError。
"""


def register_sandbox_context(name: str, cls: type) -> None:
    """注册 SandboxContext 子类供 host IPC 反序列化使用。"""
    _SANDBOX_CONTEXT_REGISTRY[name] = cls


def get_protocol_for_group(group: str) -> type | None:
    """返回 group 对应的 Protocol 类。未知 group 返回 None。"""
    return GROUP_PROTOCOL.get(group)


def get_valid_implements(group: str) -> frozenset[str]:
    """返回指定 group 的合法 implements 值集合。"""
    return _VALID_IMPLEMENTS.get(group, frozenset())


def get_tier_for_group(group: str) -> Tier:
    """返回 group 对应的 Tier。未知 group 默认 SERVICE。"""
    return _GROUP_TIER.get(group, Tier.SERVICE)


# ── Dataclass：类型定义（全部 frozen）───────────────────────────────────────────


@dataclass(frozen=True)
class CapabilityRef:
    """解析后的 capability 条目 — manifest capabilities 数组的单元素（ADR-0011 决策 1）。

    每个 capability 以 (group, name) 为唯一键，独立注册到全局注册表。
    """

    group: str
    name: str
    display_name: str
    description: str
    factory: str  # "module:attr" 格式
    implements: str  # 逻辑名，如 "Processor"
    permissions: tuple[str, ...] = ()
    constraints: dict[str, str] | None = None  # {"after": "robots", "before": "fetch"}
    input_type: None = None  # Phase 4+ 启用
    output_type: None = None  # Phase 4+ 启用
    deprecated: bool = False
    deprecated_since: str | None = None  # 废弃起始版本号，如 "0.3.0"
    deprecation_message: str = ""  # 迁移指引，如 "请迁移到 fetch-and-parse"

    @property
    def global_key(self) -> str:
        """返回全局唯一键 f"{group}/{name}"。"""
        return f"{self.group}/{self.name}"

    def validate_implements(self) -> bool:
        """两段式校验：implements 值是否在其 group 的合法值集合中。"""
        valid = get_valid_implements(self.group)
        if not valid:
            return True  # 第三方 group，引擎不校验
        return self.implements in valid


def _normalize_config_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """将 ADR 扁平 config_schema 格式规范化为 JSON Schema 格式。

    ADR-0011 决策 11 的 manifest 示例使用扁平 key→field_schema 格式（无 "properties" 包裹）。
    JSON Schema 标准格式为 {"type": "object", "properties": {...}}。
    两格式均接受，扁平格式自动包裹——下游消费者仅需处理 JSON Schema 格式。
    """
    if not schema:
        return schema
    if "properties" in schema:
        return schema  # 已是 JSON Schema 格式
    return {"type": "object", "properties": dict(schema)}


@dataclass(frozen=True)
class PluginManifest:
    """插件静态 manifest — astroframe-plugin.json 的反序列化结果（ADR-0011 决策 6）。

    manifest 是插件能力的唯一声明源（SSOT）。引擎只读，不写回。
    """

    manifest_version: int
    name: str
    requires_engine: str
    capabilities: tuple[CapabilityRef, ...] = ()
    requires_plugins: dict[str, str] = field(default_factory=dict)
    config_schema: dict[str, Any] = field(default_factory=dict)
    signing: dict[str, Any] | None = None  # {"method": "sigstore", "identity": "..."}
    processor_chain_default_order: tuple[str, ...] | None = None  # 内置独占

    def with_capabilities(self, capabilities: tuple[CapabilityRef, ...]) -> PluginManifest:
        """返回新 manifest，仅包含指定 capability 子集（用于废弃策略过滤）。"""
        return PluginManifest(
            manifest_version=self.manifest_version,
            name=self.name,
            requires_engine=self.requires_engine,
            capabilities=capabilities,
            requires_plugins=self.requires_plugins,
            config_schema=self.config_schema,
            signing=self.signing,
            processor_chain_default_order=self.processor_chain_default_order,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PluginManifest:
        """从 JSON dict 反序列化 PluginManifest。

        调用方应确保 data 已通过 S6 清洗。缺失必填字段时抛 ManifestValidationError
        而非 KeyError——错误信息对插件作者可操作。
        """
        from astroframe._errors import ManifestValidationError

        caps_raw: list[dict[str, Any]] = data.get("capabilities", [])
        capabilities: list[CapabilityRef] = []
        for i, c in enumerate(caps_raw):
            try:
                capabilities.append(
                    CapabilityRef(
                        group=c["group"],
                        name=c["name"],
                        display_name=c.get("display_name", c["name"]),
                        description=c.get("description", ""),
                        factory=c["factory"],
                        implements=c["implements"],
                        permissions=tuple(c.get("permissions", [])),
                        constraints=c.get("constraints"),
                        input_type=c.get("input_type"),
                        output_type=c.get("output_type"),
                        deprecated=c.get("deprecated", False),
                        deprecated_since=c.get("deprecated_since"),
                        deprecation_message=c.get("deprecation_message", ""),
                    )
                )
            except KeyError as exc:
                raise ManifestValidationError(f"capabilities[{i}]: 缺少必填字段 {exc.args[0]!r}") from exc

        try:
            mv = data["manifest_version"]
            nm = data["name"]
        except KeyError as exc:
            raise ManifestValidationError(f"缺少必填字段 {exc.args[0]!r}") from exc

        chain_order_raw = data.get("processor_chain_default_order")
        chain_order: tuple[str, ...] | None = None
        if chain_order_raw is not None:
            chain_order = tuple(chain_order_raw)

        return cls(
            manifest_version=mv,
            name=nm,
            requires_engine=data.get("requires_engine", ">=0.1"),
            capabilities=tuple(capabilities),
            requires_plugins=data.get("requires_plugins", {}),
            config_schema=_normalize_config_schema(data.get("config_schema", {})),
            signing=data.get("signing"),
            processor_chain_default_order=chain_order,
        )


@dataclass(frozen=True)
class PluginRef:
    """已发现插件的运行时引用 — 绑定 manifest + 状态（ADR-0011 决策 2/7/8）。"""

    manifest: PluginManifest
    status: PluginStatus
    package_name: str
    version: str
    effective_permissions: tuple[str, ...] = ()
    effective_permission_level: PermissionLevel = PermissionLevel.NORMAL

    @property
    def is_loaded(self) -> bool:
        return self.status == PluginStatus.LOADED


@dataclass(frozen=True)
class SignatureResult:
    """插件签名验证结果（ADR-0011 S17）。"""

    verified: bool
    method: str  # "sigstore", "gpg", "unsigned"
    identity: str | None = None
    error: str | None = None

    @classmethod
    def unverified(cls, reason: str = "") -> SignatureResult:
        return cls(verified=False, method="unsigned", error=reason)


# ── SandboxContext — Plugin 安全边界（ADR-0011 决策 14）────────────────────────


@dataclass(frozen=True)
class SandboxContext:
    """PluginScoped 的输入——进程内/外通用的可序列化视图。

    这是 Plugin 的安全边界。Plugin 看不到不可序列化的引擎内部对象。
    子系统通过继承添加领域字段。
    """

    plugin_id: str  # f"{package_name}/{capability_name}"
    config: dict[str, Any]  # 插件自身配置（已脱敏）
    tmp_dir: str  # /tmp/astroframe-plugin-{name}/
    engine_version: str  # 用于 requires_engine 运行时二次校验
