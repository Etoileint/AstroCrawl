"""astroframe — 通用插件平台运行时（ADR-0011）。

纯基础设施，零业务依赖。提供"发现→注册→生命周期→调度→安全"的通用平台运行时。
所有插件统一在子进程沙箱内运行（Chrome/Deno/iOS 统一沙箱原则）。
对标 pluggy (hook spec/hook impl) + Dagster (Protocol 类型契约) + Kubernetes (apiVersion 逻辑名)。

第三方扩展点注册 API:
    register_group(group: str, protocol_cls: type) -> None
        第三方注册新能力类型（对标 Kubernetes CRD）。
        定义在 _types.py（与 GROUP_PROTOCOL SSOT 同文件）。
"""

from __future__ import annotations

from astroframe._errors import (
    AstroCrawlError,
    AstroFrameError,
    CapabilityConflictError,
    ManifestValidationError,
    PluginLoadError,
    PluginSandboxError,
    PluginStateError,
    SandboxError,
    SchemaValidationError,
    SignatureError,
)
from astroframe._lifecycle import (
    ACLOSE_KILL_TIMEOUT,
    ACLOSE_SIGTERM_TIMEOUT,
    ACLOSE_TERMINATE_TIMEOUT,
    MAX_CRASH_RESTARTS,
    MAX_MSG_BYTES,
    MAX_SUBPROCESSES,
    PROCESS_TIMEOUT,
    SubprocessPlugin,
    SubprocessRegistry,
    create_plugin_instance,
    orchestrate_plugin_global,
)
from astroframe._loader import discover_plugins
from astroframe._registry import PluginRegistry
from astroframe._sandbox import (
    AuditHookProvider,
    CompositeSandbox,
    LandlockSandbox,
    PlatformCapabilities,
    ResourceLimitSandbox,
    SandboxProvider,
    SeccompBpfSandbox,
    detect_platform_capabilities,
)
from astroframe._scanner import ScanResult, ScanViolation, scan_plugin_package, scan_py_file
from astroframe._schema_validator import validate_config
from astroframe._signature import compute_package_hash, discover_verifiers, validate_signing_field, verify_plugin
from astroframe._state import PluginState
from astroframe._types import (
    _PROTOCOL_METHOD,
    GROUP_PROTOCOL,
    AuditEvent,
    BrowserHook,
    CapabilityRef,
    ChatProvider,
    CLISubcommand,
    ContentExtractor,
    DeprecationSeverity,
    ExecutionMode,
    Exporter,
    GUIPage,
    HealthCheck,
    HookKind,
    LifecycleHook,
    PermissionLevel,
    PluginGlobal,
    PluginManifest,
    PluginRef,
    PluginScoped,
    PluginStatus,
    Processor,
    RuleSource,
    SandboxContext,
    SignatureResult,
    SignatureVerifier,
    Tier,
    Transform,
    URLFilter,
    check_deprecation,
    derive_execution_mode,
    derive_permission_level,
    get_protocol_for_group,
    get_tier_for_group,
    get_valid_implements,
    permission_level_higher_than,
    register_group,
    register_sandbox_context,
)

__all__ = [
    # enums
    "HookKind",
    "PluginStatus",
    "PermissionLevel",
    "Tier",
    "ExecutionMode",
    "DeprecationSeverity",
    # protocols (15)
    "PluginGlobal",
    "PluginScoped",
    "Processor",
    "Exporter",
    "ChatProvider",
    "Transform",
    "HealthCheck",
    "RuleSource",
    "ContentExtractor",
    "CLISubcommand",
    "GUIPage",
    "BrowserHook",
    "URLFilter",
    "LifecycleHook",
    "SignatureVerifier",
    # dataclasses
    "CapabilityRef",
    "PluginManifest",
    "PluginRef",
    "SignatureResult",
    "SandboxContext",
    # group protocol SSOT
    "GROUP_PROTOCOL",
    "_PROTOCOL_METHOD",
    # group helpers
    "get_protocol_for_group",
    "get_valid_implements",
    "get_tier_for_group",
    "register_group",
    "register_sandbox_context",
    # permission derivation
    "derive_permission_level",
    "permission_level_higher_than",
    "derive_execution_mode",
    # deprecation
    "check_deprecation",
    # audit events
    "AuditEvent",
    # schema validator
    "validate_config",
    # signing
    "compute_package_hash",
    "verify_plugin",
    "validate_signing_field",
    "discover_verifiers",
    # discovery & loading
    "discover_plugins",
    # registry
    "PluginRegistry",
    # state
    "PluginState",
    # errors (10)
    "AstroFrameError",
    "AstroCrawlError",
    "PluginLoadError",
    "CapabilityConflictError",
    "SchemaValidationError",
    "SandboxError",
    "PluginSandboxError",
    "ManifestValidationError",
    "SignatureError",
    "PluginStateError",
    # Phase 2 — scanner
    "ScanViolation",
    "ScanResult",
    "scan_plugin_package",
    "scan_py_file",
    # Phase 2 — sandbox
    "SandboxProvider",
    "SeccompBpfSandbox",
    "LandlockSandbox",
    "ResourceLimitSandbox",
    "AuditHookProvider",
    "CompositeSandbox",
    "PlatformCapabilities",
    "detect_platform_capabilities",
    # Phase 2 — lifecycle
    "create_plugin_instance",
    "SubprocessPlugin",
    "SubprocessRegistry",
    "orchestrate_plugin_global",
    # Phase 2 — IPC constants
    "MAX_MSG_BYTES",
    "PROCESS_TIMEOUT",
    "ACLOSE_SIGTERM_TIMEOUT",
    "ACLOSE_TERMINATE_TIMEOUT",
    "ACLOSE_KILL_TIMEOUT",
    "MAX_SUBPROCESSES",
    "MAX_CRASH_RESTARTS",
]
