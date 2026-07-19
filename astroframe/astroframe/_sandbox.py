"""跨平台沙箱抽象（ADR-0011 S8-S14）。

提供 SandboxProvider Protocol + 平台能力检测 + Linux seccomp-bpf/Landlock/rlimit +
PEP 578 审计钩子 + macOS/Windows 信息性检测。

ctypes 使用说明：本模块通过 ctypes 调用 libc 的 syscall 接口（seccomp、Landlock）。
这是引擎基础设施，非插件代码。S5 的 ctypes 约束针对插件包。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import resource
import sys
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from astrobasis import LogfmtLogger

log = LogfmtLogger("astroframe.sandbox")

# ── rlimit 常量 ─────────────────────────────────────────────────────────────────

_RLIMIT_VALUES: dict[int, int] = {
    resource.RLIMIT_AS: 512 * 1024 * 1024,  # 512 MB
    resource.RLIMIT_NOFILE: 256,
    # RLIMIT_NPROC is deliberately NOT set here — on Linux it's a per-UID limit,
    # not per-process. Setting it to a low value prevents ALL processes of the
    # same user from creating threads (including the asyncio thread pool).
    # Fork prevention: fork/vfork are blocked; clone is conditionally allowed
    # (CLONE_VM must be set — permits thread creation, denies process fork).
}


# ── 平台能力数据类 ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PlatformCapabilities:
    """运行时平台安全能力检测结果。"""

    platform: str  # sys.platform: "linux" / "darwin" / "win32"
    arch: str  # platform.machine()
    kernel_version: str  # os.uname().release
    seccomp: bool = False
    landlock: bool = False
    user_namespaces: bool = False  # Phase 2 未实现
    app_sandbox: bool = False
    hardened_runtime: bool = False
    app_container: bool = False
    job_object: bool = False
    restricted_token: bool = False
    integrity_low: bool = False


def detect_platform_capabilities() -> PlatformCapabilities:
    """检测当前运行时平台的安全能力。"""
    plat = sys.platform
    arch = platform.machine()

    try:
        kernel_version = os.uname().release
    except AttributeError:
        kernel_version = ""

    caps = PlatformCapabilities(platform=plat, arch=arch, kernel_version=kernel_version)

    if plat == "linux":
        caps = _detect_linux_capabilities(caps, arch, kernel_version)
    elif plat == "darwin":
        caps = _detect_macos_capabilities(caps)
    elif plat == "win32":
        caps = _detect_windows_capabilities(caps)

    return caps


def _detect_linux_capabilities(caps: PlatformCapabilities, arch: str, kernel_version: str) -> PlatformCapabilities:
    """检测 Linux 平台安全能力。"""
    seccomp_ok = arch in ("x86_64", "aarch64")
    landlock_ok = _check_landlock_abi() >= 1
    user_ns_ok = _check_user_namespaces()

    return PlatformCapabilities(
        platform=caps.platform,
        arch=caps.arch,
        kernel_version=caps.kernel_version,
        seccomp=seccomp_ok,
        landlock=landlock_ok,
        user_namespaces=user_ns_ok,
    )


def _check_user_namespaces() -> bool:
    """检测 user namespaces 是否可用（Linux 3.8+）。

    /proc/self/ns/user 在支持 user namespaces 的内核上始终存在。
    """
    try:
        os.stat("/proc/self/ns/user")
        return True
    except OSError:
        return False


def _check_landlock_abi() -> int:
    """探测内核支持的 Landlock ABI 版本。返回 0 表示不可用。"""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    except OSError:
        return 0

    # Landlock syscalls (444-446) share the same numbers across all architectures —
    # they were added in Linux 5.13 as a new syscall family with unified numbering.
    SYS_LANDLOCK_CREATE_RULESET = 444

    LANDLOCK_CREATE_RULESET_VERSION = 1 << 0

    try:
        result = libc.syscall(
            SYS_LANDLOCK_CREATE_RULESET,
            None,
            0,
            LANDLOCK_CREATE_RULESET_VERSION,
        )
        if result < 0:
            return 0
        return result  # type: ignore[no-any-return]
    except (OSError, AttributeError, ValueError):
        return 0


def _detect_macos_capabilities(caps: PlatformCapabilities) -> PlatformCapabilities:
    """检测 macOS 平台安全能力。仅检测，不主动施加。"""
    app_sandbox = _check_macos_app_sandbox()
    hardened = _check_macos_hardened_runtime()
    return PlatformCapabilities(
        platform=caps.platform,
        arch=caps.arch,
        kernel_version=caps.kernel_version,
        app_sandbox=app_sandbox,
        hardened_runtime=hardened,
    )


def _check_macos_app_sandbox() -> bool:
    """检测 macOS App Sandbox 是否启用。

    主检测路径：APP_SANDBOX_CONTAINER_ID 环境变量（macOS 在沙箱进程中自动设置）。
    次检测路径：Sandbox.framework 的 sandbox_check() API。
    """
    # 环境变量检测（可靠，macOS 自动设置）
    if os.environ.get("APP_SANDBOX_CONTAINER_ID"):
        return True
    # sandbox_check() 位于 Sandbox.framework，非 libc
    try:
        sandbox = ctypes.CDLL("/System/Library/Frameworks/Sandbox.framework/Sandbox", use_errno=True)
        result = sandbox.sandbox_check(None, None, 0)
        return result == 0  # type: ignore[no-any-return]
    except (OSError, AttributeError):
        return False


def _check_macos_hardened_runtime() -> bool:
    """检测 macOS Hardened Runtime。"""
    try:
        import ctypes.util as cu

        libc = ctypes.CDLL(cu.find_library("c"), use_errno=True)
        # csops 检查
        flags = ctypes.c_uint32(0)
        result = libc.csops(0, 0, ctypes.byref(flags), ctypes.sizeof(flags))
        return result == 0  # type: ignore[no-any-return]
    except (OSError, AttributeError):
        return False


def _detect_windows_capabilities(caps: PlatformCapabilities) -> PlatformCapabilities:
    """检测 Windows 平台安全能力。仅检测，不主动施加。"""
    job_obj = _check_windows_job_object()
    return PlatformCapabilities(
        platform=caps.platform,
        arch=caps.arch,
        kernel_version=caps.kernel_version,
        job_object=job_obj,
        restricted_token=True,  # 所有分发方式可用
        integrity_low=True,
    )


def _check_windows_job_object() -> bool:
    """检测 Windows Job Object 成员身份。"""
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # Windows BOOL = 4 字节 int（非 C _Bool = 1 字节）
        result = kernel32.IsProcessInJob(ctypes.c_void_p(-1), None, ctypes.byref(ctypes.c_int()))
        return result != 0  # type: ignore[no-any-return]
    except (AttributeError, OSError):
        return False


# ── SandboxProvider Protocol ───────────────────────────────────────────────────


@runtime_checkable
class SandboxProvider(Protocol):
    """沙箱提供者 Protocol——每个沙箱层独立实现。"""

    @staticmethod
    def available() -> bool: ...

    def setup(self) -> None: ...  # 准备沙箱资源（编译 BPF 等）

    def apply_to_process(self) -> None: ...  # 在当前进程施加沙箱约束

    def teardown(self) -> None: ...  # 清理


# ── ResourceLimitSandbox ───────────────────────────────────────────────────────


class ResourceLimitSandbox:
    """rlimit 资源限制沙箱（Linux 通用，所有平台可用）。"""

    @staticmethod
    def available() -> bool:
        return True

    def setup(self) -> None:
        pass

    def apply_to_process(self) -> None:
        for rlimit_type, value in _RLIMIT_VALUES.items():
            try:
                resource.setrlimit(rlimit_type, (value, value))
            except (OSError, ValueError) as exc:
                log.warning(
                    "plugin_sandbox_rlimit_failed",
                    rlimit=str(rlimit_type),
                    value=value,
                    error=str(exc),
                )

    def teardown(self) -> None:
        pass


# ── SeccompBpfSandbox (Linux) ──────────────────────────────────────────────────

# Syscall number tables — x86_64
_SYSCALL_X86_64: dict[str, int] = {
    # Group A — File I/O
    "read": 0,
    "write": 1,
    "openat": 257,
    "openat2": 437,
    "close": 3,
    "lseek": 8,
    "pread64": 17,
    "pwrite64": 18,
    "readv": 19,
    "writev": 20,
    "dup3": 292,
    # Group B — File metadata
    "fstat": 5,
    "newfstatat": 262,
    "statx": 332,
    "getdents64": 217,
    "lgetxattr": 192,
    "fgetxattr": 193,
    # Group C — Memory
    "mmap": 9,
    "mprotect": 10,
    "munmap": 11,
    "brk": 12,
    "mremap": 25,
    "madvise": 28,
    "mlock": 149,
    # Group D — Thread & sync
    "futex": 202,
    "set_robust_list": 273,
    "set_tid_address": 218,
    "tgkill": 234,
    "rt_sigaction": 13,
    "rt_sigprocmask": 14,
    "rt_sigreturn": 15,
    "sched_yield": 24,
    "sched_getaffinity": 204,
    # Group E — glibc modern
    "getrandom": 318,
    "rseq": 334,
    "close_range": 436,
    # Group F — Process & network
    "getrlimit": 97,
    "prlimit64": 302,
    "getpid": 39,
    "gettid": 186,
    "getuid": 102,
    "getgid": 104,
    "prctl": 157,
    "seccomp": 317,
    "exit": 60,
    "exit_group": 231,
    "arch_prctl": 158,
    "clone": 56,
    # Event polling
    "epoll_create1": 291,
    "epoll_ctl": 233,
    "epoll_pwait": 281,
    "epoll_pwait2": 441,
    "eventfd2": 290,
    # Network
    "socket": 41,
    "connect": 42,
    "bind": 49,
    "listen": 50,
    "accept": 43,
    "accept4": 288,
    "sendto": 44,
    "recvfrom": 45,
    "sendmsg": 46,
    "recvmsg": 47,
    "recvmmsg": 299,
    "getsockname": 51,
    "getpeername": 52,
    "setsockopt": 54,
    "getsockopt": 55,
    "shutdown": 48,
    "fcntl": 72,
    "poll": 7,
    "ppoll": 271,
    # Group G — Landlock
    "landlock_create_ruleset": 444,
    "landlock_add_rule": 445,
    "landlock_restrict_self": 446,
}

# Syscall number tables — aarch64
_SYSCALL_AARCH64: dict[str, int] = {
    # Group A — File I/O
    "read": 63,
    "write": 64,
    "openat": 56,
    "openat2": 437,
    "close": 57,
    "lseek": 62,
    "pread64": 67,
    "pwrite64": 68,
    "readv": 65,
    "writev": 66,
    "dup3": 24,
    # Group B — File metadata
    "fstat": 80,
    "newfstatat": 79,
    "statx": 291,
    "getdents64": 217,
    "lgetxattr": 9,
    "fgetxattr": 10,
    # Group C — Memory
    "mmap": 222,
    "mprotect": 226,
    "munmap": 215,
    "brk": 214,
    "mremap": 216,
    "madvise": 233,
    "mlock": 229,
    # Group D — Thread & sync
    "futex": 98,
    "set_robust_list": 99,
    "set_tid_address": 96,
    "tgkill": 131,
    "rt_sigaction": 134,
    "rt_sigprocmask": 135,
    "rt_sigreturn": 139,
    "sched_yield": 124,
    "sched_getaffinity": 123,
    # Group E — glibc modern
    "getrandom": 278,
    "rseq": 293,
    "close_range": 436,
    # Group F — Process & network
    "getrlimit": 163,
    "prlimit64": 261,
    "getpid": 172,
    "gettid": 178,
    "getuid": 174,
    "getgid": 176,
    "prctl": 167,
    "seccomp": 277,
    "exit": 93,
    "exit_group": 94,
    "clone": 220,
    # Event polling
    "epoll_create1": 20,
    "epoll_ctl": 21,
    "epoll_pwait": 22,
    "epoll_pwait2": 441,
    "eventfd2": 290,
    # Network
    "socket": 198,
    "connect": 203,
    "bind": 200,
    "listen": 201,
    "accept": 202,
    "accept4": 242,
    "sendto": 206,
    "recvfrom": 207,
    "sendmsg": 211,
    "recvmsg": 212,
    "recvmmsg": 243,
    "getsockname": 204,
    "getpeername": 205,
    "setsockopt": 208,
    "getsockopt": 209,
    "shutdown": 210,
    "fcntl": 25,
    "poll": 73,
    "ppoll": 271,
    # Group G — Landlock
    "landlock_create_ruleset": 444,
    "landlock_add_rule": 445,
    "landlock_restrict_self": 446,
}

# 显式拒绝列表（动作为 KILL）
_BLOCKED_SYSCALLS: frozenset[str] = frozenset(
    {
        "ptrace",
        "perf_event_open",
        "bpf",
        "kexec_load",
        "iopl",  # x86_64 only
        "ioperm",  # x86_64 only
        "init_module",
        "finit_module",
        "delete_module",
        "io_uring_setup",
        "io_uring_enter",
        "io_uring_register",
        "userfaultfd",
        "fork",
        "vfork",
        "execve",
        "execveat",
        "unshare",
        "setns",
        "mount",
        "umount2",
        "pivot_root",
        "chroot",
        "kexec_file_load",
        "pidfd_getfd",
    }
)


class SeccompBpfSandbox:
    """Linux seccomp-bpf syscall 白名单沙箱（S9）。

    优先使用 libseccomp Python 绑定（内部维护多架构映射表）。
    不可用时回退至手写 ctypes + BPF 方案。
    """

    def __init__(self) -> None:
        self._table: dict[str, int] = {}
        self._arch = platform.machine()
        self._using_libseccomp = False
        self._filter: Any = None

    @staticmethod
    def available() -> bool:
        if sys.platform != "linux":
            return False
        arch = platform.machine()
        if arch not in ("x86_64", "aarch64"):
            return False
        # 探测内核 seccomp 是否实际可用（CONFIG_SECCOMP=y + 未通过 seccomp=off 禁用）
        # /proc/sys/kernel/seccomp/actions_avail 仅在 seccomp 可用的内核上存在（Linux 4.14+）
        if not os.path.exists("/proc/sys/kernel/seccomp/actions_avail"):
            # 回退：4.14 之前的内核上，尝试 prctl(PR_GET_SECCOMP)
            try:
                libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
                # PR_GET_SECCOMP 在 seccomp 不可用时返回 -1 并设置 errno=EINVAL
                result = libc.prctl(2, 0, 0, 0, 0)  # PR_GET_SECCOMP = 2
                if result == -1 and ctypes.get_errno() == 22:  # EINVAL
                    return False
            except (OSError, AttributeError):
                return False
        return True

    def setup(self) -> None:
        """准备 BPF 程序。"""
        if self._arch == "aarch64":
            self._table = dict(_SYSCALL_AARCH64)
        else:
            self._table = dict(_SYSCALL_X86_64)

        # 尝试使用 libseccomp
        try:
            import libseccomp  # type: ignore[import-untyped]

            self._filter = libseccomp.SyscallFilter(libseccomp.ERRNO(38))  # ENOSYS default
            for _name, allowed_nr in self._table.items():
                if _name == "clone":
                    continue  # clone 由下方 CLONE_VM 条件规则单独处理
                self._filter.add_rule(libseccomp.ALLOW, allowed_nr)
            # clone: CLONE_VM 置位 → ALLOW（线程创建）；否则 → KILL（进程 fork）
            # libseccomp 按参数过滤器数量排优先级：条件 ALLOW（1 arg）> 无条件 KILL（0 arg）
            clone_nr = self._table.get("clone")
            if clone_nr is not None:
                self._filter.add_rule(
                    libseccomp.ALLOW,
                    "clone",
                    libseccomp.Arg(0, libseccomp.MASK_EQ, 0x00000100, 0x00000100),
                )
                # 显式 KILL —— 仅当 CLONE_VM 条件不满足时触发
                # 对标 ADR S9:842 "clone (unflagged)" 显式拒绝 + BPF 路径 KILL_PROCESS 一致性
                self._filter.add_rule(libseccomp.KILL, "clone")
            for name in _BLOCKED_SYSCALLS:
                blocked_nr = self._table.get(name)
                if blocked_nr is not None:
                    self._filter.add_rule(libseccomp.KILL, blocked_nr)
            self._using_libseccomp = True
            log.info(
                "plugin_sandbox_layer_active",
                layer="seccomp-bpf",
                arch=self._arch,
                abi_version="libseccomp",
            )
        except ImportError:
            self._using_libseccomp = False
            log.info(
                "plugin_sandbox_layer_active",
                layer="seccomp-bpf",
                arch=self._arch,
                abi_version="ctypes-bpf",
            )

    def apply_to_process(self) -> None:
        """在当前进程安装 seccomp filter。（不可变顺序约束）"""
        if not self.available():
            return

        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

        # 步骤 1: prctl(NO_NEW_PRIVS) — 必须在 seccomp 之前
        PR_SET_NO_NEW_PRIVS = 36
        try:
            result = libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
            if result != 0:
                log.warning("plugin_sandbox_layer_failed", layer="seccomp-no-new-privs")
                return
        except (OSError, AttributeError) as exc:
            log.warning("plugin_sandbox_layer_failed", layer="seccomp-no-new-privs", error=str(exc))
            return

        # 步骤 2: seccomp(SECCOMP_SET_MODE_FILTER)
        if self._using_libseccomp and self._filter is not None:
            try:
                self._filter.load()
                return  # 加载成功 → 跳过 BPF 回退
            except OSError as exc:
                log.error(
                    "plugin_sandbox_layer_failed",
                    layer="seccomp-bpf-load",
                    error=str(exc),
                )
            # 加载失败 → 继续执行手写 BPF 回退

        # 手写 BPF 回退
        try:
            prog = self._build_bpf_prog()
            if prog is None:
                log.warning("plugin_sandbox_layer_failed", layer="seccomp-bpf-build")
                return

            SECCOMP_SET_MODE_FILTER = 1
            SECCOMP_FILTER_FLAG_TSYNC = 1 << 0

            class sock_fprog(ctypes.Structure):
                _fields_ = [
                    ("len", ctypes.c_uint16),
                    ("filter", ctypes.c_void_p),
                ]

            fprog = sock_fprog(len=len(prog), filter=ctypes.cast(prog, ctypes.c_void_p))
            seccomp_nr = self._table.get("seccomp", 317)  # 架构感知——x86_64=317, aarch64=277
            result = libc.syscall(seccomp_nr, SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_TSYNC, ctypes.byref(fprog))
            if result != 0:
                log.warning("plugin_sandbox_layer_failed", layer="seccomp-bpf-install", errno=ctypes.get_errno())
        except (OSError, AttributeError) as exc:
            log.warning("plugin_sandbox_layer_failed", layer="seccomp-bpf", error=str(exc))

    def _build_bpf_prog(self) -> Any | None:
        """手写 BPF 程序——返回 ctypes sock_filter 数组。"""
        try:
            import struct
        except ImportError:
            return None

        BPF_LD = 0x00
        BPF_JMP = 0x05
        BPF_RET = 0x06
        BPF_ALU = 0x04
        BPF_W = 0x00
        BPF_ABS = 0x20
        BPF_JEQ = 0x10
        BPF_K = 0x00
        BPF_AND = 0x50  # BPF_ALU | (AND_opcode << 4) = 0x04 | 0x50

        SECCOMP_RET_KILL_PROCESS = 0x80000000
        SECCOMP_RET_ERRNO = 0x00050000
        SECCOMP_RET_ALLOW = 0x7FFF0000

        # clone 从白名单中排除——由 CLONE_VM 条件检查单独处理
        clone_nr = self._table.get("clone")
        allowed_nrs = sorted({nr for name, nr in self._table.items() if name != "clone"})
        blocked_nrs = sorted({self._table[n] for n in _BLOCKED_SYSCALLS if n in self._table})

        insns: list[tuple[int, int, int, int]] = []

        # 加载 syscall number (seccomp_data.nr 偏移为 0)
        insns.append((BPF_LD | BPF_W | BPF_ABS, 0, 0, 0))

        # ── clone 条件检查（CLONE_VM 必须置位——允许线程，拒绝 fork）──
        if clone_nr is not None:
            # JEQ clone_nr: if nr != clone → skip to main scan (jt=0, jf=5)
            CLONE_VM = 0x00000100
            insns.append((BPF_JMP | BPF_JEQ | BPF_K, 0, 5, clone_nr))
            # A = seccomp_data.args[0] (clone flags, low 32 bits at offset 16)
            insns.append((BPF_LD | BPF_W | BPF_ABS, 0, 0, 16))
            # A = A & CLONE_VM
            insns.append((BPF_ALU | BPF_AND | BPF_K, 0, 0, CLONE_VM))
            # JEQ 0: if (flags & CLONE_VM) == 0 → KILL else → ALLOW
            # BPF JEQ (jt=0, jf=1): A==0 → execute next (KILL); A!=0 → skip 1 (ALLOW)
            insns.append((BPF_JMP | BPF_JEQ | BPF_K, 0, 1, 0))
            insns.append((BPF_RET | BPF_K, 0, 0, SECCOMP_RET_KILL_PROCESS))
            insns.append((BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ALLOW))

        # 线性扫描白名单
        for nr in allowed_nrs:
            insns.append((BPF_JMP | BPF_JEQ | BPF_K, 0, 1, nr))
            insns.append((BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ALLOW))

        # 显式拒绝列表 → KILL
        for nr in blocked_nrs:
            insns.append((BPF_JMP | BPF_JEQ | BPF_K, 0, 1, nr))
            insns.append((BPF_RET | BPF_K, 0, 0, SECCOMP_RET_KILL_PROCESS))

        # 默认动作 → ENOSYS (ERRNO 38)
        insns.append((BPF_RET | BPF_K, 0, 0, SECCOMP_RET_ERRNO | 38))

        # 打包为 sock_filter 数组: (code: H, jt: B, jf: B, k: I)
        fmt = "HBBI"
        buf = b"".join(struct.pack(fmt, code, jt, jf, k) for code, jt, jf, k in insns)
        return ctypes.create_string_buffer(buf)

    def teardown(self) -> None:
        pass


# ── LandlockSandbox (Linux) ───────────────────────────────────────────────────


class LandlockSandbox:
    """Linux Landlock 文件系统 ACL 沙箱（S10）。

    所有路径由父进程解析为绝对路径后传入。子进程只做 landlock_add_rule()。
    """

    def __init__(self, readonly_paths: list[str], readwrite_paths: list[str]) -> None:
        self._readonly = readonly_paths
        self._readwrite = readwrite_paths
        self._abi = 0

    @staticmethod
    def available() -> bool:
        if sys.platform != "linux":
            return False
        return _check_landlock_abi() >= 1

    def setup(self) -> None:
        self._abi = _check_landlock_abi()
        if self._abi >= 1:
            log.info(
                "plugin_sandbox_layer_active",
                layer="landlock",
                abi_version=str(self._abi),
                arch=platform.machine(),
            )

    def apply_to_process(self) -> None:
        """应用 Landlock FS ACL（启动序列步骤 4）。运行时失败 → log ERROR + 继续执行。"""
        if not self.available():
            return

        if self._abi < 1:
            log.warning("plugin_sandbox_layer_failed", layer="landlock", reason="ABI < 1")
            return

        try:
            libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        except OSError as exc:
            log.error("plugin_sandbox_layer_failed", layer="landlock", error=str(exc))
            return

        LANDLOCK_ACCESS_FS_READ_FILE = 1 << 0
        LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
        LANDLOCK_ACCESS_FS_READ_DIR = 1 << 2
        LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 3
        LANDLOCK_ACCESS_FS_EXECUTE = 1 << 4

        # handled_access_fs: 声明 Landlock 管理的访问类型——
        # 未显式允许的已声明访问类型将被全局拒绝
        handled_access_fs = (
            LANDLOCK_ACCESS_FS_READ_FILE
            | LANDLOCK_ACCESS_FS_WRITE_FILE
            | LANDLOCK_ACCESS_FS_READ_DIR
            | LANDLOCK_ACCESS_FS_REMOVE_FILE
            | LANDLOCK_ACCESS_FS_EXECUTE
        )

        # landlock_ruleset_attr: { __u64 handled_access_fs }
        class LandlockRulesetAttr(ctypes.Structure):
            _fields_ = [("handled_access_fs", ctypes.c_uint64)]

        ruleset_attr = LandlockRulesetAttr(handled_access_fs=handled_access_fs)
        # flags=0 → 创建规则集（非版本探测）；attr/size 指定访问控制范围
        try:
            ruleset_fd = libc.syscall(
                444,  # SYS_landlock_create_ruleset
                ctypes.byref(ruleset_attr),
                ctypes.sizeof(ruleset_attr),
                0,  # flags=0: create ruleset, not version probe
            )
            if ruleset_fd < 0:
                err = ctypes.get_errno()
                log.error(
                    "plugin_sandbox_layer_failed",
                    layer="landlock",
                    reason=f"create_ruleset failed (errno={err})",
                )
                return
        except (OSError, AttributeError) as exc:
            log.error("plugin_sandbox_layer_failed", layer="landlock", error=str(exc))
            return

        readonly_access = LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR | LANDLOCK_ACCESS_FS_EXECUTE
        readwrite_access = readonly_access | LANDLOCK_ACCESS_FS_WRITE_FILE | LANDLOCK_ACCESS_FS_REMOVE_FILE

        # 添加只读路径
        for path_str in self._readonly:
            self._add_landlock_rule(libc, ruleset_fd, path_str, readonly_access)

        # 添加读写路径
        for path_str in self._readwrite:
            self._add_landlock_rule(libc, ruleset_fd, path_str, readwrite_access)

        # 限制自身
        try:
            result = libc.syscall(446, ruleset_fd, 0)  # SYS_landlock_restrict_self
            os.close(ruleset_fd)
            if result != 0:
                log.error(
                    "plugin_sandbox_layer_failed",
                    layer="landlock",
                    reason=f"restrict_self failed (errno={ctypes.get_errno()})",
                )
        except (OSError, AttributeError) as exc:
            log.error("plugin_sandbox_layer_failed", layer="landlock", error=str(exc))
            try:
                os.close(ruleset_fd)
            except OSError:
                pass

    @staticmethod
    def _add_landlock_rule(libc: ctypes.CDLL, ruleset_fd: int, path_str: str, access: int) -> None:
        """添加单条 Landlock 路径访问规则。

        Landlock LANDLOCK_RULE_PATH_BENEATH 通过目录 fd 指定路径——
        parent_fd 必须是目标目录的已打开文件描述符，不是 AT_FDCWD。
        """
        dir_fd = -1
        try:
            # O_PATH: 不需要读权限，仅用于路径引用（Landlock 的语义要求）
            # O_CLOEXEC: fork 安全
            try:
                dir_fd = os.open(path_str, os.O_PATH | os.O_CLOEXEC)
            except (OSError, PermissionError):
                # 某些路径可能不可访问——跳过该规则的添加
                log.debug(
                    "plugin_sandbox_landlock_path_open_failed",
                    path=path_str,
                )
                return

            class LandlockPathBeneathAttr(ctypes.Structure):
                _fields_ = [
                    ("allowed_access", ctypes.c_uint64),
                    ("parent_fd", ctypes.c_int32),
                ]

            attr = LandlockPathBeneathAttr(allowed_access=access, parent_fd=dir_fd)

            result = libc.syscall(
                445,  # SYS_landlock_add_rule
                ruleset_fd,
                1,  # LANDLOCK_RULE_PATH_BENEATH
                ctypes.byref(attr),
                0,
            )
            if result != 0:
                log.debug(
                    "plugin_sandbox_landlock_rule_failed",
                    path=path_str,
                    errno=ctypes.get_errno(),
                )
        except (OSError, AttributeError) as exc:
            log.debug("plugin_sandbox_landlock_rule_failed", path=path_str, error=str(exc))
        finally:
            if dir_fd >= 0:
                try:
                    os.close(dir_fd)
                except OSError:
                    pass

    def teardown(self) -> None:
        pass


# ── PEP 578 AuditHookProvider ──────────────────────────────────────────────────


class AuditHookProvider:
    """PEP 578 审计钩子——契约验证层（S8）。

    角色：S5 声称"此插件不 import socket"→ 运行时 audit hook 做全量 import 日志。
    S5 漏检但运行时发现 import socket → WARNING 级安全事件。
    PEP 578 是第二双眼睛，不是第二把锁。
    """

    def __init__(self) -> None:
        self._sentinel = False

    @staticmethod
    def available() -> bool:
        return True

    def setup(self) -> None:
        self._sentinel = False

    def apply_to_process(self) -> None:
        """安装 PEP 578 audit hook。子进程启动序列步骤 4（CompositeSandbox 统一安装）。"""
        self._sentinel = False
        sys.addaudithook(self._audit_handler)

    def teardown(self) -> None:
        """关闭审计钩子（sentinel flag——CPython 不支持移除 audit hooks）。"""
        self._sentinel = True

    def _audit_handler(self, event: str, args: tuple[Any, ...]) -> None:
        if self._sentinel:
            return

        # import 事件——全量记录（INFO）
        if event == "import":
            if args:
                module_name = str(args[0])
                log.info("plugin_audit_import", module=module_name)

        # 阻断事件（ERROR — ADR S16:907）
        elif event in ("exec", "compile", "os.system", "os.fork", "subprocess.Popen") or event.startswith("os.spawn"):
            log.error("plugin_audit_blocked", event=event)
        elif event.startswith("os.exec"):  # os.execv, os.execve, os.execvp, os.execvpe
            log.error("plugin_audit_blocked", event=event)

        # pickle/marshal 阻断（ERROR — 无条件硬阻断）
        elif event in ("pickle.find_class", "marshal.loads"):
            log.error("plugin_audit_blocked", event=event)

        # 审计钩子篡改检测（ERROR — ADR S8:820）
        elif event == "sys.addaudithook":
            log.error("plugin_audit_blocked", event=event, reason="audit_hook_tampering")

        # ctypes 检测（WARNING）
        elif event == "ctypes.dlopen":
            log.warning("plugin_audit_import", event=event)

        # socket 检测（WARNING）
        elif event.startswith("socket."):
            log.warning("plugin_audit_import", event=event, module="socket")

        # sqlite3 检测（WARNING）
        elif event == "sqlite3.connect":
            log.warning("plugin_audit_import", event=event, module="sqlite3")

        # urllib 检测（INFO）
        elif event == "urllib.Request":
            log.info("plugin_data_egress", event=event)

        # 文件系统事件（INFO — 安全相关操作，可审计追溯）
        elif event in (
            "open",
            "os.listdir",
            "os.scandir",
            "os.walk",
            "os.mkdir",
            "os.rmdir",
            "os.remove",
            "os.rename",
            "os.unlink",
            "shutil.rmtree",
            "tempfile.mkstemp",
            "tempfile.mkdtemp",
            "glob.glob",
            "pathlib.glob",
        ):
            log.info("plugin_audit_event", event=event)

        # fcntl / os.chmod / os.chown / os.putenv / mmap — INFO
        elif event.startswith(("fcntl.", "os.chmod", "os.chown", "os.putenv", "mmap.")):
            log.info("plugin_audit_event", event=event)

        # catch-all — 剩余 ADR S8 审计事件（全量覆盖）
        else:
            log.debug("plugin_audit_event", event=event)


# ── CompositeSandbox ──────────────────────────────────────────────────────────


class CompositeSandbox:
    """组合沙箱——按序施加所有可用的沙箱 provider。

    apply_to_process() 顺序：
    1. ResourceLimitSandbox (rlimit)
    2. SeccompBpfSandbox (NO_NEW_PRIVS → seccomp filter)
    3. LandlockSandbox (FS ACL)
    4. AuditHookProvider (PEP 578)
    """

    def __init__(self, providers: list[SandboxProvider]) -> None:
        self._providers = providers

    def setup(self) -> None:
        for provider in self._providers:
            try:
                provider.setup()
            except Exception as exc:
                log.error(
                    "plugin_sandbox_setup_failed",
                    provider=type(provider).__name__,
                    error=str(exc),
                )

    def apply_to_process(self) -> None:
        for provider in self._providers:
            try:
                if provider.available():
                    provider.apply_to_process()
            except Exception as exc:
                log.error(
                    "plugin_sandbox_apply_failed",
                    provider=type(provider).__name__,
                    error=str(exc),
                )

    def teardown(self) -> None:
        # 逆序清理
        for provider in reversed(self._providers):
            try:
                provider.teardown()
            except Exception as exc:
                log.error(
                    "plugin_sandbox_teardown_failed",
                    provider=type(provider).__name__,
                    error=str(exc),
                )


# ── macOS 信息性检测类 ─────────────────────────────────────────────────────────


class MacOSSandboxInfo:
    """macOS App Sandbox 信息性检测（S11）。仅检测，不主动施加。

    pip 安装方式下 App Sandbox 不可用——需 .app bundle + Apple Developer 签名。
    """

    @staticmethod
    def available() -> bool:
        return sys.platform == "darwin" and _check_macos_app_sandbox()

    def setup(self) -> None:
        pass

    def apply_to_process(self) -> None:
        pass  # no-op: 需系统级 entitlements + Apple Developer 签名

    def teardown(self) -> None:
        pass


# ── Windows 信息性检测类 ───────────────────────────────────────────────────────


class WindowsSandboxInfo:
    """Windows 沙箱信息性检测（S12）。仅检测，不主动施加。"""

    @staticmethod
    def available() -> bool:
        return sys.platform == "win32"

    def setup(self) -> None:
        pass

    def apply_to_process(self) -> None:
        pass  # no-op: pip 安装方式无 syscall 过滤机制

    def teardown(self) -> None:
        pass
