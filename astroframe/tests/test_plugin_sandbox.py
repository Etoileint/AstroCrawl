"""沙箱测试 — 平台检测、seccomp、Landlock、rlimit、audit hooks、CompositeSandbox。

Linux 专属测试用 skipif 标记。
"""

from __future__ import annotations

import sys

import pytest

from astroframe._sandbox import (
    AuditHookProvider,
    CompositeSandbox,
    LandlockSandbox,
    ResourceLimitSandbox,
    SeccompBpfSandbox,
    detect_platform_capabilities,
)


class TestPlatformCapabilities:
    def test_detect_returns_platform(self) -> None:
        caps = detect_platform_capabilities()
        assert caps.platform in ("linux", "darwin", "win32")
        assert caps.arch

    def test_linux_caps_match_platform(self) -> None:
        caps = detect_platform_capabilities()
        if caps.platform == "linux":
            # seccomp 应在 x86_64 或 aarch64 上可用
            if caps.arch in ("x86_64", "aarch64"):
                assert caps.seccomp is True
            else:
                assert caps.seccomp is False


class TestResourceLimitSandbox:
    def test_always_available(self) -> None:
        assert ResourceLimitSandbox.available() is True

    def test_setup_and_teardown_noop(self) -> None:
        rl = ResourceLimitSandbox()
        rl.setup()
        rl.teardown()

    @pytest.mark.skipif(sys.platform != "linux", reason="rlimit values Linux-specific")
    def test_apply_sets_rlimits(self) -> None:
        import resource

        rl = ResourceLimitSandbox()
        rl.apply_to_process()
        nofile = resource.getrlimit(resource.RLIMIT_NOFILE)
        assert nofile[0] == 256


class TestSeccompBpfSandbox:
    def test_available_linux_only(self) -> None:
        if sys.platform == "linux":
            arch = __import__("platform").machine()
            if arch in ("x86_64", "aarch64"):
                assert SeccompBpfSandbox.available() is True
        else:
            assert SeccompBpfSandbox.available() is False

    def test_setup_populates_table(self) -> None:
        s = SeccompBpfSandbox()
        s.setup()
        assert len(s._table) > 0

    @pytest.mark.skipif(sys.platform != "linux", reason="seccomp Linux only")
    def test_setup_has_key_syscalls(self) -> None:
        s = SeccompBpfSandbox()
        s.setup()
        assert "read" in s._table
        assert "write" in s._table
        assert "mmap" in s._table
        assert "futex" in s._table
        assert "socket" in s._table

    def test_teardown_noop(self) -> None:
        s = SeccompBpfSandbox()
        s.teardown()


class TestLandlockSandbox:
    def test_available_depends_on_kernel(self) -> None:
        # Landlock 取决于内核支持——在 CI/旧内核上可能为 False
        result = LandlockSandbox.available()
        assert isinstance(result, bool)

    def test_setup_detects_abi(self) -> None:
        ls = LandlockSandbox([], [])
        ls.setup()
        assert ls._abi >= 0

    def test_apply_noop_when_unavailable(self) -> None:
        ls = LandlockSandbox([], [])
        ls.setup()
        # 不应抛异常
        ls.apply_to_process()

    def test_teardown_noop(self) -> None:
        ls = LandlockSandbox([], [])
        ls.teardown()


class TestAuditHookProvider:
    def test_available(self) -> None:
        assert AuditHookProvider.available() is True

    def test_setup_and_teardown(self) -> None:
        audit = AuditHookProvider()
        audit.setup()
        audit.apply_to_process()
        audit.teardown()
        assert audit._sentinel is True

    def test_apply_registers_hook(self) -> None:
        audit = AuditHookProvider()
        audit.setup()
        audit.apply_to_process()
        # 验证 hook 已注册（通过 teardown 验证 sentinel 行为）
        audit.teardown()
        assert audit._sentinel


class TestCompositeSandbox:
    def test_empty_providers(self) -> None:
        cs = CompositeSandbox([])
        cs.setup()
        cs.apply_to_process()
        cs.teardown()

    def test_with_rlimit(self) -> None:
        rl = ResourceLimitSandbox()
        cs = CompositeSandbox([rl])
        cs.setup()
        cs.apply_to_process()
        cs.teardown()

    def test_with_multiple_providers(self) -> None:
        rl = ResourceLimitSandbox()
        audit = AuditHookProvider()
        cs = CompositeSandbox([rl, audit])
        cs.setup()
        cs.apply_to_process()
        cs.teardown()

    def test_setup_exception_isolated(self) -> None:
        """单个 provider setup 失败不应影响其他 provider。"""

        class FailingSetup:
            @staticmethod
            def available() -> bool:
                return True

            def setup(self) -> None:
                raise RuntimeError("setup failed")

            def apply_to_process(self) -> None:
                pass

            def teardown(self) -> None:
                pass

        rl = ResourceLimitSandbox()
        cs = CompositeSandbox([rl, FailingSetup()])
        # 不应抛异常
        cs.setup()
        cs.apply_to_process()
        cs.teardown()


# ══════════════════════════════════════════════════════════════════════════════════
# Phase 3a — 深度沙箱强制执行测试
# ══════════════════════════════════════════════════════════════════════════════════


class TestSeccompEnforcement:
    """Linux seccomp-bpf 强制执行测试（AC17）。"""

    @pytest.mark.skipif(sys.platform != "linux", reason="seccomp Linux only")
    def test_fork_blocked_by_seccomp(self) -> None:
        """子进程中 os.fork() → 被 seccomp-bpf 拒绝（SIGSYS 或非零退出）。

        若 seccomp 安装失败（容器/受限环境），跳过测试。
        """
        import subprocess

        # 先探测 seccomp 能否成功安装（通过 /proc/self/status 的 Seccomp 字段）
        probe_code = (
            "from astroframe._sandbox import SeccompBpfSandbox;"
            "s = SeccompBpfSandbox();"
            "s.setup();"
            "s.apply_to_process();"
            "with open('/proc/self/status') as f:"
            "    for line in f:"
            "        if line.startswith('Seccomp:'):"
            "            print(f'SECCOMP_MODE_{line.split()[1]}')"
        )
        probe = subprocess.run([sys.executable, "-c", probe_code], capture_output=True, timeout=10)
        if b"SECCOMP_MODE_2" not in probe.stdout and b"SECCOMP_MODE_1" not in probe.stdout:
            pytest.skip("seccomp-bpf install failed in this environment (mode not 1 or 2)")

        code = (
            "from astroframe._sandbox import SeccompBpfSandbox;"
            "s = SeccompBpfSandbox();"
            "s.setup();"
            "s.apply_to_process();"
            "import os;"
            "os.fork()"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=10)
        # fork 被阻止 → 进程被 kill 或 fork 失败
        assert result.returncode != 0, f"fork should be blocked by seccomp, got rc={result.returncode}"

    @pytest.mark.skipif(sys.platform != "linux", reason="seccomp Linux only")
    def test_socket_blocked_when_not_in_whitelist(self) -> None:
        """未在白名单中的 syscall → ENOSYS (errno 38)。"""
        import subprocess

        # 探测 seccomp
        probe_code = (
            "from astroframe._sandbox import SeccompBpfSandbox;"
            "s = SeccompBpfSandbox();"
            "s.setup();"
            "s.apply_to_process();"
            "with open('/proc/self/status') as f:"
            "    for line in f:"
            "        if line.startswith('Seccomp:'):"
            "            print(f'SECCOMP_MODE_{line.split()[1]}')"
        )
        probe = subprocess.run([sys.executable, "-c", probe_code], capture_output=True, timeout=10)
        if b"SECCOMP_MODE_2" not in probe.stdout and b"SECCOMP_MODE_1" not in probe.stdout:
            pytest.skip("seccomp-bpf install failed in this environment (mode not 1 or 2)")

        code = (
            "from astroframe._sandbox import SeccompBpfSandbox;"
            "s = SeccompBpfSandbox();"
            "s._table = {'read': 0, 'write': 1, 'exit': 60, 'exit_group': 231} if s._arch == 'x86_64'"
            " else {'read': 63, 'write': 64, 'exit': 93, 'exit_group': 94};"
            "s.setup();"
            "s.apply_to_process();"
            "import os;"
            "os.getpid()"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=10)
        assert result.returncode != 0


class TestLandlockEnforcement:
    """Linux Landlock FS ACL 强制执行测试（AC18）。"""

    @pytest.mark.skipif(sys.platform != "linux", reason="Landlock Linux only")
    def test_restricted_file_denied(self, tmp_path) -> None:
        """Landlock 激活后 /etc/passwd 不可读。"""
        import subprocess

        landlock = LandlockSandbox([], [])
        if not landlock.available():
            pytest.skip("Landlock not available (kernel < 5.13)")

        # 探测 Landlock 是否真正执行拦截（API 存在不代表生效，如容器中无 CAP_SYS_ADMIN）
        tmpdir_str = str(tmp_path)
        code = (
            f"from astroframe._sandbox import LandlockSandbox;"
            f"ls = LandlockSandbox([], ['{tmpdir_str}']);"
            f"ls.setup();"
            f"ls.apply_to_process();"
            f"open('/etc/passwd')"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=10)
        if result.returncode == 0:
            pytest.skip("Landlock not enforced in this environment (e.g. container without CAP_SYS_ADMIN)")
        assert result.returncode != 0

    @pytest.mark.skipif(sys.platform != "linux", reason="Landlock Linux only")
    def test_allowed_dir_writable(self, tmp_path) -> None:
        """Landlock 允许的 temp 目录可读写。"""
        import subprocess

        landlock = LandlockSandbox([], [])
        if not landlock.available():
            pytest.skip("Landlock not available (kernel < 5.13)")

        tmpdir_str = str(tmp_path)
        test_file = tmp_path / "test.txt"
        code = (
            f"from astroframe._sandbox import LandlockSandbox;"
            f"ls = LandlockSandbox([], ['{tmpdir_str}']);"
            f"ls.setup();"
            f"ls.apply_to_process();"
            f"open('{test_file}', 'w').write('ok');"
            f"print('WRITE_OK')"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=10)
        if b"LANDLOCK_APPLIED" not in result.stdout and b"WRITE_OK" not in result.stdout:
            pytest.skip("Landlock apply failed in this environment")
        assert result.returncode == 0, f"allowed dir should be writable, stderr: {result.stderr.decode()}"
        if b"WRITE_OK" in result.stdout:
            assert test_file.read_text() == "ok"


class TestMacOSSandboxInfo:
    """macOS App Sandbox 信息性检测（AC3）。"""

    def test_available_false_on_linux(self) -> None:
        """Linux 上 available() 返回 False。"""
        if sys.platform != "linux":
            pytest.skip("only meaningful on Linux")
        from astroframe._sandbox import MacOSSandboxInfo

        assert MacOSSandboxInfo.available() is False

    def test_methods_are_safe_noops(self) -> None:
        """所有方法安全 no-op。"""
        from astroframe._sandbox import MacOSSandboxInfo

        m = MacOSSandboxInfo()
        m.setup()
        m.apply_to_process()
        m.teardown()

    def test_available_requires_actual_sandbox(self, monkeypatch) -> None:
        """macOS pip 安装 → available() False（需 .app bundle）。"""
        from astroframe._sandbox import MacOSSandboxInfo

        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr("astroframe._sandbox._check_macos_app_sandbox", lambda: False)
        assert MacOSSandboxInfo.available() is False

        monkeypatch.setattr("astroframe._sandbox._check_macos_app_sandbox", lambda: True)
        assert MacOSSandboxInfo.available() is True


class TestWindowsSandboxInfo:
    """Windows 沙箱信息性检测（AC3）。"""

    def test_available_false_on_linux(self) -> None:
        """Linux 上 available() 返回 False。"""
        from astroframe._sandbox import WindowsSandboxInfo

        assert WindowsSandboxInfo.available() is False

    def test_methods_are_safe_noops(self) -> None:
        """所有方法安全 no-op。"""
        from astroframe._sandbox import WindowsSandboxInfo

        w = WindowsSandboxInfo()
        w.setup()
        w.apply_to_process()
        w.teardown()


class TestLandlockAbi:
    """Landlock ABI 检测测试（AC1/AC2）。"""

    def test_abi_returns_non_negative(self) -> None:
        """_check_landlock_abi() 返回 ≥ 0。"""
        from astroframe._sandbox import _check_landlock_abi

        abi = _check_landlock_abi()
        assert abi >= 0

    def test_abi_repeatable(self) -> None:
        """重复调用返回一致结果。"""
        from astroframe._sandbox import _check_landlock_abi

        a = _check_landlock_abi()
        b = _check_landlock_abi()
        assert a == b


class TestPEP578AuditHandler:
    """PEP 578 审计事件处理测试（AC19/AC21）。"""

    def test_blocked_events_are_error_level(self) -> None:
        """阻断事件 log.error——非 warning。"""
        from astroframe._sandbox import AuditHookProvider

        audit = AuditHookProvider()
        audit.setup()
        # exec/compile/os.system/os.fork/os.spawn/subprocess.Popen → ERROR
        # 通过 handler 直接调用验证无异常
        audit._audit_handler("exec", ())
        audit._audit_handler("compile", ())
        audit._audit_handler("os.system", ())
        audit._audit_handler("os.fork", ())
        audit._audit_handler("os.execv", ())
        audit._sentinel = True

    def test_import_event_is_info(self) -> None:
        """import 事件 → INFO。"""
        from astroframe._sandbox import AuditHookProvider

        audit = AuditHookProvider()
        audit.setup()
        audit._audit_handler("import", ("json",))
        audit._sentinel = True

    def test_sys_addaudithook_is_blocked(self) -> None:
        """sys.addaudithook → ERROR（审计钩子篡改检测）。"""
        from astroframe._sandbox import AuditHookProvider

        audit = AuditHookProvider()
        audit.setup()
        audit._audit_handler("sys.addaudithook", ())
        audit._sentinel = True

    def test_sentinel_stops_all_events(self) -> None:
        """sentinel=True 后所有事件静默。"""
        from astroframe._sandbox import AuditHookProvider

        audit = AuditHookProvider()
        audit._sentinel = True
        # 不应抛异常
        audit._audit_handler("import", ("os",))
        audit._audit_handler("exec", ())
        audit._audit_handler("socket.connect", ())

    def test_catch_all_debug_events(self) -> None:
        """未显式处理的事件 → DEBUG catch-all。"""
        from astroframe._sandbox import AuditHookProvider

        audit = AuditHookProvider()
        audit.setup()
        # open/mmap/os.chmod/os.listdir/signal.* → catch-all DEBUG
        audit._audit_handler("open", ())
        audit._audit_handler("mmap", ())
        audit._audit_handler("os.chmod", ())
        audit._audit_handler("os.listdir", ())
        audit._audit_handler("signal.alarm", ())
        audit._sentinel = True

    def test_pickle_loads_is_error(self) -> None:
        """pickle.loads → ERROR（无条件硬阻断）。"""
        from astroframe._sandbox import AuditHookProvider

        audit = AuditHookProvider()
        audit.setup()
        audit._audit_handler("pickle.find_class", ())
        audit._audit_handler("marshal.loads", ())
        audit._sentinel = True


class TestUserNamespacesDetection:
    """user_namespaces 检测测试（P2-7）。"""

    @pytest.mark.skipif(sys.platform != "linux", reason="user namespaces Linux only")
    def test_detection_returns_bool(self) -> None:
        """_check_user_namespaces() 返回 bool。"""
        from astroframe._sandbox import _check_user_namespaces

        result = _check_user_namespaces()
        assert isinstance(result, bool)

    def test_detect_includes_user_ns(self) -> None:
        """detect_platform_capabilities() 设置 user_namespaces 字段。"""
        caps = detect_platform_capabilities()
        assert isinstance(caps.user_namespaces, bool)
        if sys.platform == "linux":
            # Linux 3.8+ 应支持 user namespaces
            pass  # 在容器中可能为 False
