"""插件系统错误层次 — 9 个异常类，全部继承 AstroFrameError。"""

from __future__ import annotations


class AstroFrameError(Exception):
    """所有 astroframe 异常的基类。"""

    pass


class PluginLoadError(AstroFrameError):
    """插件加载失败——import 抛异常或工厂不可调用。"""

    pass


class CapabilityConflictError(AstroFrameError):
    """capability (group, name) 全局唯一冲突。"""

    pass


class SchemaValidationError(AstroFrameError):
    """JSON Schema 校验失败——插件 config 不满足 manifest config_schema。"""

    pass


class SandboxError(AstroFrameError):
    """沙箱基础设施错误——沙箱启动失败、seccomp 安装失败等。"""

    pass


class PluginSandboxError(AstroFrameError):
    """子进程沙箱执行错误——子进程崩溃、IPC 断开、超时。"""

    pass


class ManifestValidationError(AstroFrameError):
    """manifest 格式/内容校验失败——缺少必填字段、非法值、S6 清洗不通过。"""

    pass


class SignatureError(AstroFrameError):
    """插件签名验证失败。"""

    pass


class PluginStateError(AstroFrameError):
    """插件状态冲突——对 LOADED 插件做 enable、对 DISABLED 插件做 disable 等。"""

    pass


AstroCrawlError = AstroFrameError
"""向后兼容别名——ADR-0014 迁移至独立平台包后的遗留名称。"""
