"""测试: astrocrawl/browser/_device_caps.py — 设备 GPU 能力检测。

get_chromium_flags() 封装 _is_llvmpipe() 检测结果，为 Chromium 推荐渲染后端。
仅在 llvmpipe 软件渲染设备注入 --use-gl=swiftshader。
"""

from __future__ import annotations

import subprocess
from unittest import mock

from astrocrawl.browser._device_caps import _is_llvmpipe, get_chromium_flags

# ═══════════════════════════════════════════════════════════════════════
# get_chromium_flags
# ═══════════════════════════════════════════════════════════════════════


class TestGetChromiumFlags:
    def test_returns_swiftshader_when_llvmpipe(self):
        """llvmpipe 检测到时返回 SwiftShader flag。"""
        with mock.patch("astrocrawl.browser._device_caps._is_llvmpipe", return_value=True):
            result = get_chromium_flags()
        assert result == ["--use-gl=swiftshader"]

    def test_returns_empty_when_no_llvmpipe(self):
        """无 llvmpipe 时返回空列表。"""
        with mock.patch("astrocrawl.browser._device_caps._is_llvmpipe", return_value=False):
            result = get_chromium_flags()
        assert result == []

    def test_returns_new_list_each_call(self):
        """每次调用返回新 list 对象，避免调用方意外共享。"""
        with mock.patch("astrocrawl.browser._device_caps._is_llvmpipe", return_value=True):
            r1 = get_chromium_flags()
            r2 = get_chromium_flags()
        assert r1 is not r2
        assert r1 == r2


# ═══════════════════════════════════════════════════════════════════════
# _is_llvmpipe
# ═══════════════════════════════════════════════════════════════════════


class TestIsLlvmpipeDetection:
    def test_true_when_stdout_contains_llvmpipe(self):
        """glxinfo 输出含 llvmpipe → True。"""
        proc = mock.MagicMock()
        proc.stdout = "Device: llvmpipe (LLVM 15.0.7, 256 bits)\n"
        with mock.patch("subprocess.run", return_value=proc):
            assert _is_llvmpipe() is True

    def test_false_when_stdout_no_llvmpipe(self):
        """glxinfo 输出不含 llvmpipe → False (正常 GPU)。"""
        proc = mock.MagicMock()
        proc.stdout = "Device: AMD Radeon RX 7900 XTX\n"
        with mock.patch("subprocess.run", return_value=proc):
            assert _is_llvmpipe() is False

    def test_false_when_stdout_empty(self):
        """glxinfo 输出为空 → False。"""
        proc = mock.MagicMock()
        proc.stdout = ""
        with mock.patch("subprocess.run", return_value=proc):
            assert _is_llvmpipe() is False


class TestIsLlvmpipeErrorHandling:
    def test_false_on_file_not_found(self):
        """glxinfo 未安装 → False (fail-safe)。"""
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            assert _is_llvmpipe() is False

    def test_false_on_timeout(self):
        """glxinfo 超时 → False (fail-safe)。"""
        with mock.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["glxinfo", "-B"], timeout=2),
        ):
            assert _is_llvmpipe() is False

    def test_false_on_generic_exception(self):
        """任意其他异常 → False (fail-safe)。"""
        with mock.patch("subprocess.run", side_effect=OSError):
            assert _is_llvmpipe() is False


class TestIsLlvmpipeSubprocessArgs:
    def test_passes_glxinfo_b_flag(self):
        """调用 glxinfo -B。"""
        with mock.patch("subprocess.run") as mock_run:
            _is_llvmpipe()
        mock_run.assert_called_once_with(["glxinfo", "-B"], capture_output=True, text=True, timeout=2)

    def test_timeout_is_2_seconds(self):
        """超时=2s — 避免卡死在无响应的 glxinfo。"""
        with mock.patch("subprocess.run") as mock_run:
            _is_llvmpipe()
        assert mock_run.call_args.kwargs["timeout"] == 2
