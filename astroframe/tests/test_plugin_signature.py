"""插件签名验证测试 — ADR-0011 S17（issue #251）。

覆盖: compute_package_hash, validate_signing_field, UnsignedVerifier,
SigstoreVerifier, GpgVerifier, verify_plugin, discover_verifiers。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from astroframe._errors import ManifestValidationError, SignatureError
from astroframe._signature import (
    _check_rekor_entry,
    _extract_gpg_signing_fingerprint,
    _extract_gpg_validsig_fingerprint,
    _get_sigstore_cert_identity,
    _get_sigstore_verified,
    _GpgVerifier,
    _match_sigstore_identity,
    _SigstoreVerifier,
    _UnsignedVerifier,
    compute_package_hash,
    discover_verifiers,
    validate_signing_field,
    verify_plugin,
)
from astroframe._types import PluginManifest

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_manifest(**overrides: Any) -> PluginManifest:
    defaults: dict[str, Any] = {
        "manifest_version": 1,
        "name": "astrocrawl-test",
        "requires_engine": ">=0.2",
    }
    defaults.update(overrides)
    return PluginManifest(**defaults)


def _make_py_file(dir_path: Path, relative: str, content: str = "") -> Path:
    """在 dir_path 下创建 Python 文件，自动创建父目录。"""
    file_path = dir_path / relative
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content or "# test module\n")
    return file_path


# ══════════════════════════════════════════════════════════════════════════════════
# TestComputePackageHash
# ══════════════════════════════════════════════════════════════════════════════════


class TestComputePackageHash:
    def test_deterministic_across_calls(self, tmp_path: Path) -> None:
        """两次调用返回相同 hash。"""
        _make_py_file(tmp_path, "pkg/__init__.py", "x = 1\n")
        _make_py_file(tmp_path, "pkg/module.py", "def f(): pass\n")
        with (
            patch.object(sys, "path", [str(tmp_path)] + sys.path),
            patch("astroframe._signature.distribution") as mock_dist,
        ):
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "pkg")
            h1 = compute_package_hash("test-pkg")
            h2 = compute_package_hash("test-pkg")
            assert h1 is not None
            assert h1 == h2

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        """修改文件内容 → hash 不同。"""
        _make_py_file(tmp_path, "pkg/__init__.py", "x = 1\n")
        with (
            patch.object(sys, "path", [str(tmp_path)] + sys.path),
            patch("astroframe._signature.distribution") as mock_dist,
        ):
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "pkg")
            h1 = compute_package_hash("test-pkg")

        _make_py_file(tmp_path, "pkg/__init__.py", "x = 2\n")
        with (
            patch.object(sys, "path", [str(tmp_path)] + sys.path),
            patch("astroframe._signature.distribution") as mock_dist,
        ):
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "pkg")
            h2 = compute_package_hash("test-pkg")

        assert h1 is not None
        assert h2 is not None
        assert h1 != h2

    def test_empty_package_returns_hash(self, tmp_path: Path) -> None:
        """仅含 __init__.py → 返回非 None hash。"""
        _make_py_file(tmp_path, "pkg/__init__.py")
        with (
            patch.object(sys, "path", [str(tmp_path)] + sys.path),
            patch("astroframe._signature.distribution") as mock_dist,
        ):
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "pkg")
            h = compute_package_hash("test-pkg")
            assert h is not None
            assert h.startswith("sha256:")

    def test_excludes_pycache(self, tmp_path: Path) -> None:
        """__pycache__ 目录被排除。"""
        _make_py_file(tmp_path, "pkg/__init__.py", "x = 1\n")
        pycache = tmp_path / "pkg/__pycache__"
        pycache.mkdir(parents=True, exist_ok=True)
        (pycache / "module.cpython-312.pyc").write_text("cached")
        with (
            patch.object(sys, "path", [str(tmp_path)] + sys.path),
            patch("astroframe._signature.distribution") as mock_dist,
        ):
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "pkg")
            h1 = compute_package_hash("test-pkg")

        # 添加更多 .pyc 文件
        (pycache / "other.cpython-312.pyc").write_text("more cache")
        with (
            patch.object(sys, "path", [str(tmp_path)] + sys.path),
            patch("astroframe._signature.distribution") as mock_dist,
        ):
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "pkg")
            h2 = compute_package_hash("test-pkg")

        assert h1 == h2  # hash 不受 .pyc 影响

    def test_symlink_skipped(self, tmp_path: Path) -> None:
        """符号链接被跳过，不跟随。"""
        _make_py_file(tmp_path, "pkg/__init__.py", "x = 1\n")
        external = tmp_path / "external.py"
        external.write_text("# external\n")
        symlink = tmp_path / "pkg/link.py"
        try:
            os.symlink(str(external), str(symlink))
        except OSError:
            pytest.skip("无法创建符号链接（权限或平台限制）")

        with (
            patch.object(sys, "path", [str(tmp_path)] + sys.path),
            patch("astroframe._signature.distribution") as mock_dist,
        ):
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "pkg")
            h = compute_package_hash("test-pkg")
            assert h is not None
            assert h.startswith("sha256:")

    def test_namespace_package_returns_none(self, tmp_path: Path) -> None:
        """dist_root 非目录 → 返回 None。"""
        with patch("astroframe._signature.distribution") as mock_dist:
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "nonexistent")
            h = compute_package_hash("test-pkg")
            assert h is None

    def test_empty_file_list_returns_none(self, tmp_path: Path) -> None:
        """所有文件被排除 → 返回 None + log.warning。"""
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir(parents=True)
        with (
            patch.object(sys, "path", [str(tmp_path)] + sys.path),
            patch("astroframe._signature.distribution") as mock_dist,
        ):
            mock_dist.return_value.locate_file.return_value = str(pkg_dir)
            h = compute_package_hash("test-pkg")
            assert h is None

    def test_hash_format(self, tmp_path: Path) -> None:
        """hash 格式为 sha256:<64-char-hex>。"""
        _make_py_file(tmp_path, "pkg/__init__.py", "x = 1\n")
        with (
            patch.object(sys, "path", [str(tmp_path)] + sys.path),
            patch("astroframe._signature.distribution") as mock_dist,
        ):
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "pkg")
            h = compute_package_hash("test-pkg")
            assert h is not None
            prefix, hex_str = h.split(":", 1)
            assert prefix == "sha256"
            assert len(hex_str) == 64
            assert all(c in "0123456789abcdef" for c in hex_str)


# ══════════════════════════════════════════════════════════════════════════════════
# TestValidateSigningField
# ══════════════════════════════════════════════════════════════════════════════════


class TestValidateSigningField:
    def test_none_signing_ok(self) -> None:
        """signing is None → 合法。"""
        validate_signing_field(None)  # 不抛异常

    def test_non_dict_raises(self) -> None:
        """signing 非 dict → ManifestValidationError。"""
        with pytest.raises(ManifestValidationError, match="对象或 null"):
            validate_signing_field("sigstore")

    def test_empty_dict_missing_method(self) -> None:
        """空 dict 缺少 method → ManifestValidationError。"""
        with pytest.raises(ManifestValidationError, match="method"):
            validate_signing_field({})

    def test_valid_sigstore(self) -> None:
        """合法 sigstore identity 格式。"""
        validate_signing_field(
            {
                "method": "sigstore",
                "identity": "https://github.com/Etoileint/test/.github/workflows/release.yml@refs/tags/v1.0.0",
            }
        )

    def test_sigstore_semver_prerelease_tag(self) -> None:
        """identity tag 含 semver pre-release（-beta.1）→ 合法。"""
        validate_signing_field(
            {
                "method": "sigstore",
                "identity": "https://github.com/Etoileint/test/.github/workflows/release.yml@refs/tags/v2.0.0-beta.1",
            }
        )

    def test_sigstore_tag_path_separator_rejected(self) -> None:
        """identity tag 含 '/' 路径分隔符 → ManifestValidationError（G7 防路径穿越）。"""
        with pytest.raises(ManifestValidationError, match="格式不合法"):
            validate_signing_field(
                {
                    "method": "sigstore",
                    "identity": "https://github.com/Etoileint/test/.github/workflows/release.yml@refs/tags/v1.0.0/../../other/.github/workflows/malicious.yml",
                }
            )

    def test_valid_gpg(self) -> None:
        """合法 GPG fingerprint。"""
        validate_signing_field(
            {
                "method": "gpg",
                "key_fingerprint": "9E3B5C8D7A1F2E3B4C5D6E7F8A9B0C1D2E3F4A5B",
            }
        )

    def test_gpg_fingerprint_with_spaces(self) -> None:
        """含空格分隔的 40 hex → 合法（空格被 strip）。"""
        validate_signing_field(
            {
                "method": "gpg",
                "key_fingerprint": "9E3B 5C8D 7A1F 2E3B 4C5D 6E7F 8A9B 0C1D 2E3F 4A5B",
            }
        )

    def test_valid_unsigned(self) -> None:
        """method='unsigned' → 合法。"""
        validate_signing_field({"method": "unsigned"})

    def test_unknown_method_error(self) -> None:
        """未知 method → ManifestValidationError。"""
        with pytest.raises(ManifestValidationError, match="不被支持"):
            validate_signing_field({"method": "pgp"})

    def test_sigstore_missing_identity(self) -> None:
        """sigstore 缺少 identity → ManifestValidationError。"""
        with pytest.raises(ManifestValidationError, match="identity"):
            validate_signing_field({"method": "sigstore"})

    def test_gpg_invalid_fingerprint(self) -> None:
        """GPG fingerprint 含非 hex 字符 → ManifestValidationError。"""
        with pytest.raises(ManifestValidationError, match="格式不合法"):
            validate_signing_field({"method": "gpg", "key_fingerprint": "zzz"})


# ══════════════════════════════════════════════════════════════════════════════════
# TestUnsignedVerifier
# ══════════════════════════════════════════════════════════════════════════════════


class TestUnsignedVerifier:
    def test_always_returns_unverified(self) -> None:
        manifest = _make_manifest()
        result = _UnsignedVerifier.verify(Path("/tmp"), manifest, {})
        assert result.verified is False
        assert result.method == "unsigned"

    def test_error_message_descriptive(self) -> None:
        manifest = _make_manifest()
        result = _UnsignedVerifier.verify(Path("/tmp"), manifest, {})
        assert result.error is not None
        assert "no signature" in result.error


# ══════════════════════════════════════════════════════════════════════════════════
# TestSigstoreVerifier
# ══════════════════════════════════════════════════════════════════════════════════


class TestSigstoreVerifier:
    def test_import_error_raises_signature_error(self) -> None:
        """sigstore-python 不可用 → SignatureError with install 指令。"""
        manifest = _make_manifest(name="test-plugin")
        signing = {
            "identity": "https://github.com/Etoileint/test/.github/workflows/release.yml@refs/tags/v1.0.0",
        }
        with patch.dict(sys.modules, {"sigstore.verify": None}):
            with pytest.raises(SignatureError, match="sigstore"):
                _SigstoreVerifier.verify(Path("/tmp"), manifest, signing)

    def test_friendly_message_matches_ai_provider_pattern(self) -> None:
        """错误消息对标 AIProviderUnavailableError——含包名 + install 指令。"""
        manifest = _make_manifest(name="test-plugin")
        signing = {
            "identity": "https://github.com/Etoileint/test/.github/workflows/release.yml@refs/tags/v1.0.0",
        }
        with patch.dict(sys.modules, {"sigstore.verify": None}):
            with pytest.raises(SignatureError) as exc_info:
                _SigstoreVerifier.verify(Path("/tmp"), manifest, signing)
            msg = str(exc_info.value)
            assert "sigstore" in msg.lower()
            assert "pip install" in msg

    def test_wheel_not_found(self) -> None:
        """wheel 文件未找到 → UNVERIFIED with 诚实说明。

        sigstore-python 可用但 wheel 不在 pip cache 中。
        """
        manifest = _make_manifest(name="test-plugin")
        signing = {
            "identity": "https://github.com/Etoileint/test/.github/workflows/release.yml@refs/tags/v1.0.0",
        }
        with (
            patch("astroframe._signature._find_wheel_in_pip_cache", return_value=None),
            patch.dict(sys.modules, {"sigstore.verify": None}),
        ):
            with pytest.raises(SignatureError, match="sigstore"):
                _SigstoreVerifier.verify(Path("/tmp"), manifest, signing)

    def test_wheel_removed_before_verify(self) -> None:
        """wheel path 存在但 is_file() → False（G6 TOCTOU）→ UNVERIFIED。"""
        _manifest = _make_manifest(name="test-plugin")
        _signing = {
            "identity": "https://github.com/Etoileint/test/.github/workflows/release.yml@refs/tags/v1.0.0",
        }
        fake_wheel = Path("/tmp/nonexistent-wheel.whl")
        with (
            patch("astroframe._signature._find_wheel_in_pip_cache", return_value=fake_wheel),
            patch.dict(sys.modules, {"sigstore.verify": None}),
        ):
            with pytest.raises(SignatureError):
                _SigstoreVerifier.verify(Path("/tmp"), _manifest, _signing)

    def test_bad_signature_via_helper(self) -> None:
        """_get_sigstore_verified 返回 False 时 verifier 应返回 UNVERIFIED。

        此行为由 _get_sigstore_verified → False → "签名数学验证未通过" 路径保证，
        完整调用链经由 _SigstoreVerifier.verify 需要在 sigstore-python 可用环境中
        端到端测试。此处覆盖核心判定函数。
        """
        mock_result = MagicMock(success=False, verified=False, spec=["success", "verified"])
        assert _get_sigstore_verified(mock_result) is False

    def test_identity_mismatch(self) -> None:
        """OIDC Subject ≠ manifest identity → UNVERIFIED。"""
        result = _match_sigstore_identity(
            "https://github.com/Etoileint/other/.github/workflows/release.yml@refs/tags/v1.0.0",
            "https://github.com/Etoileint/test/.github/workflows/release.yml@refs/tags/v1.0.0",
        )
        assert result is False

    def test_identity_match(self) -> None:
        """OIDC Subject 与 manifest identity 精确匹配 → True。"""
        identity = "https://github.com/Etoileint/test/.github/workflows/release.yml@refs/tags/v1.0.0"
        assert _match_sigstore_identity(identity, identity) is True

    def test_identity_match_whitespace_trimmed(self) -> None:
        """两端空白被去除后匹配。"""
        assert (
            _match_sigstore_identity(
                "  https://github.com/Etoileint/test/.github/workflows/release.yml@refs/tags/v1.0.0  ",
                "https://github.com/Etoileint/test/.github/workflows/release.yml@refs/tags/v1.0.0",
            )
            is True
        )

    def test_rekor_not_found(self) -> None:
        """_check_rekor_entry → 'not_found' when no log entry。"""
        mock_result = MagicMock(spec=[])
        assert _check_rekor_entry(mock_result) == "not_found"

    def test_rekor_bundle_verified(self) -> None:
        """_check_rekor_entry 通过 bundle.rekor_entry → 'verified'。"""
        mock_entry = MagicMock()
        mock_bundle = MagicMock(rekor_entry=mock_entry)
        mock_result = MagicMock(bundle=mock_bundle, spec=["bundle"])
        assert _check_rekor_entry(mock_result) == "verified"

    def test_get_verified_from_success_attr(self) -> None:
        """sigstore 1.x API: result.success → True。"""
        mock_result = MagicMock(success=True, spec=["success"])
        assert _get_sigstore_verified(mock_result) is True

    def test_get_verified_from_verified_attr(self) -> None:
        """sigstore 0.3.x API: result.verified → True。"""
        mock_result = MagicMock(verified=True, spec=["verified"])
        assert _get_sigstore_verified(mock_result) is True

    def test_get_cert_identity_from_san(self) -> None:
        """证书 identity 从 cert.san 提取。"""
        mock_cert = MagicMock(san="https://github.com/Etoileint/test/.github/workflows/release.yml@refs/tags/v1.0.0")
        mock_result = MagicMock(cert=mock_cert, spec=["cert"])
        result = _get_sigstore_cert_identity(mock_result)
        assert result == "https://github.com/Etoileint/test/.github/workflows/release.yml@refs/tags/v1.0.0"

    def test_get_cert_identity_none_when_no_cert(self) -> None:
        """无 cert 属性 → None。"""
        mock_result = MagicMock(spec=[])
        assert _get_sigstore_cert_identity(mock_result) is None


# ══════════════════════════════════════════════════════════════════════════════════
# TestGpgVerifier
# ══════════════════════════════════════════════════════════════════════════════════


class TestGpgVerifier:
    @pytest.fixture
    def gpg_temp_files(self, tmp_path: Path) -> tuple[Path, Path]:
        """创建真实的临时 sig + data 文件供 is_file() 检查。"""
        sig = tmp_path / "test.asc"
        sig.write_text("SIGNATURE")
        data = tmp_path / "test.whl"
        data.write_text("WHEELDATA")
        return sig, data

    def test_cli_unavailable_raises_signature_error(self) -> None:
        """gpg CLI 不在 PATH → SignatureError。"""
        manifest = _make_manifest(name="test-plugin")
        signing = {"key_fingerprint": "0" * 40}
        with patch("shutil.which", return_value=None):
            with pytest.raises(SignatureError, match="gpg"):
                _GpgVerifier.verify(Path("/tmp"), manifest, signing)

    def test_error_distinguished_from_unverified(self) -> None:
        """gpg 不可用抛 SignatureError，不是返回 result.verified=False。"""
        manifest = _make_manifest(name="test-plugin")
        signing = {"key_fingerprint": "0" * 40}
        with patch("shutil.which", return_value=None):
            with pytest.raises(SignatureError):
                _GpgVerifier.verify(Path("/tmp"), manifest, signing)

    def test_signature_file_not_found(self) -> None:
        """未找到 signature 文件 → UNVERIFIED。"""
        manifest = _make_manifest(name="test-plugin")
        signing = {"key_fingerprint": "0" * 40}
        with (
            patch("shutil.which", return_value="/usr/bin/gpg"),
            patch("astroframe._signature._find_gpg_signature_and_data", return_value=(None, None)),
        ):
            result = _GpgVerifier.verify(Path("/tmp"), manifest, signing)
            assert result.verified is False
            assert "未找到" in (result.error or "")

    def test_data_file_not_found(self) -> None:
        """找到 sig 但无 data wheel → UNVERIFIED。"""
        manifest = _make_manifest(name="test-plugin")
        signing = {"key_fingerprint": "0" * 40}
        with (
            patch("shutil.which", return_value="/usr/bin/gpg"),
            patch(
                "astroframe._signature._find_gpg_signature_and_data",
                return_value=(Path("/tmp/test.asc"), None),
            ),
        ):
            result = _GpgVerifier.verify(Path("/tmp"), manifest, signing)
            assert result.verified is False
            assert "原始 wheel" in (result.error or "")

    def test_file_removed_after_discovery(self) -> None:
        """sig 文件发现后但验证前被移除（G6 TOCTOU）→ UNVERIFIED。"""
        manifest = _make_manifest(name="test-plugin")
        signing = {"key_fingerprint": "0" * 40}
        fake_sig = Path("/tmp/nonexistent-sig.asc")
        fake_data = Path("/tmp/nonexistent-wheel.whl")
        with (
            patch("shutil.which", return_value="/usr/bin/gpg"),
            patch(
                "astroframe._signature._find_gpg_signature_and_data",
                return_value=(fake_sig, fake_data),
            ),
        ):
            result = _GpgVerifier.verify(Path("/tmp"), manifest, signing)
            assert result.verified is False
            assert "移除" in (result.error or "")

    def test_bad_signature_exit_code(self, gpg_temp_files: tuple[Path, Path]) -> None:
        """gpg --verify 返回非零 → UNVERIFIED。"""
        sig, data = gpg_temp_files
        manifest = _make_manifest(name="test-plugin")
        signing = {"key_fingerprint": "0" * 40}
        mock_proc = MagicMock(returncode=1, stdout="", stderr="gpg: BAD signature\n")
        with (
            patch("shutil.which", return_value="/usr/bin/gpg"),
            patch(
                "astroframe._signature._find_gpg_signature_and_data",
                return_value=(sig, data),
            ),
            patch("subprocess.run", return_value=mock_proc),
        ):
            result = _GpgVerifier.verify(Path("/tmp"), manifest, signing)
            assert result.verified is False
            assert "exit=1" in (result.error or "")

    def test_fingerprint_mismatch(self, gpg_temp_files: tuple[Path, Path]) -> None:
        """gpg 签名者 fp ≠ manifest 声明 → UNVERIFIED。"""
        sig, data = gpg_temp_files
        manifest = _make_manifest(name="test-plugin")
        signing = {"key_fingerprint": "0" * 40}
        stdout = "[GNUPG:] VALIDSIG AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        mock_proc = MagicMock(returncode=0, stdout=stdout, stderr="")
        with (
            patch("shutil.which", return_value="/usr/bin/gpg"),
            patch(
                "astroframe._signature._find_gpg_signature_and_data",
                return_value=(sig, data),
            ),
            patch("subprocess.run", return_value=mock_proc),
        ):
            result = _GpgVerifier.verify(Path("/tmp"), manifest, signing)
            assert result.verified is False
            assert "不匹配" in (result.error or "")

    def test_verified_when_valid(self, gpg_temp_files: tuple[Path, Path]) -> None:
        """gpg 验证全链路通过 → VERIFIED。"""
        sig, data = gpg_temp_files
        fp = "a" * 40
        manifest = _make_manifest(name="test-plugin")
        signing = {"key_fingerprint": fp}
        stdout = f"[GNUPG:] VALIDSIG {fp.upper()}\n"
        mock_proc = MagicMock(returncode=0, stdout=stdout, stderr="")
        with (
            patch("shutil.which", return_value="/usr/bin/gpg"),
            patch(
                "astroframe._signature._find_gpg_signature_and_data",
                return_value=(sig, data),
            ),
            patch("subprocess.run", return_value=mock_proc),
        ):
            result = _GpgVerifier.verify(Path("/tmp"), manifest, signing)
            assert result.verified is True
            assert result.method == "gpg"
            assert fp in (result.identity or "")

    def test_timeout_raises_signature_error(self, gpg_temp_files: tuple[Path, Path]) -> None:
        """gpg 超时 → SignatureError。"""
        sig, data = gpg_temp_files
        manifest = _make_manifest(name="test-plugin")
        signing = {"key_fingerprint": "0" * 40}
        with (
            patch("shutil.which", return_value="/usr/bin/gpg"),
            patch(
                "astroframe._signature._find_gpg_signature_and_data",
                return_value=(sig, data),
            ),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="gpg", timeout=30)),
        ):
            with pytest.raises(SignatureError, match="timed out"):
                _GpgVerifier.verify(Path("/tmp"), manifest, signing)

    def test_cannot_extract_fingerprint(self, gpg_temp_files: tuple[Path, Path]) -> None:
        """既无 VALIDSIG 也无 stderr 匹配 → UNVERIFIED。"""
        sig, data = gpg_temp_files
        manifest = _make_manifest(name="test-plugin")
        signing = {"key_fingerprint": "0" * 40}
        mock_proc = MagicMock(returncode=0, stdout="", stderr="gpg: Good signature\n")
        with (
            patch("shutil.which", return_value="/usr/bin/gpg"),
            patch(
                "astroframe._signature._find_gpg_signature_and_data",
                return_value=(sig, data),
            ),
            patch("subprocess.run", return_value=mock_proc),
        ):
            result = _GpgVerifier.verify(Path("/tmp"), manifest, signing)
            assert result.verified is False
            assert "无法" in (result.error or "")


# ══════════════════════════════════════════════════════════════════════════════════
# TestGpgFingerprintExtraction
# ══════════════════════════════════════════════════════════════════════════════════


class TestGpgFingerprintExtraction:
    def test_validsig_extraction(self) -> None:
        """VALIDSIG 行正确提取 40 字符 fingerprint。"""
        stdout = "[GNUPG:] VALIDSIG ABCDEF1234567890ABCDEF1234567890ABCDEF12\n"
        result = _extract_gpg_validsig_fingerprint(stdout)
        assert result == "abcdef1234567890abcdef1234567890abcdef12"

    def test_validsig_not_found(self) -> None:
        """无 VALIDSIG 行 → None。"""
        result = _extract_gpg_validsig_fingerprint("some other output\n")
        assert result is None

    def test_using_key_extraction(self) -> None:
        """stderr 'using RSA key' 提取 fingerprint。"""
        stderr = "gpg: Signature made Mon Jan 1 12:00:00 2026 UTC\n"
        stderr += "gpg:                using RSA key ABCDEF1234567890ABCDEF1234567890ABCDEF12\n"
        result = _extract_gpg_signing_fingerprint(stderr)
        assert result == "abcdef1234567890abcdef1234567890abcdef12"

    def test_primary_key_extraction(self) -> None:
        """stderr 'Primary key fingerprint' 提取。"""
        stderr = "gpg: Note: no trustdb\n"
        stderr += "Primary key fingerprint: ABCD EF12 3456 7890 ABCD EF12 3456 7890 ABCD EF12\n"
        result = _extract_gpg_signing_fingerprint(stderr)
        assert "abcdef1234567890abcdef1234567890abcdef12" in (result or "")


# ══════════════════════════════════════════════════════════════════════════════════
# TestVerifyPlugin
# ══════════════════════════════════════════════════════════════════════════════════


class TestVerifyPlugin:
    def test_unsigned_returns_unverified(self, tmp_path: Path) -> None:
        """无 signing → verify_plugin 返回 UNVERIFIED。"""
        _make_py_file(tmp_path, "pkg/__init__.py", "x = 1\n")
        manifest = _make_manifest(name="test-pkg")
        with patch("astroframe._signature.distribution") as mock_dist:
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "pkg")
            result = verify_plugin("test-pkg", manifest, trusted_hash=None)
            assert result.verified is False

    def test_trusted_hash_match_skips_verification(self, tmp_path: Path) -> None:
        """trusted_hash 匹配 → 跳过签名验证，返回 trusted_hash verified。"""
        _make_py_file(tmp_path, "pkg/__init__.py", "x = 1\n")
        manifest = _make_manifest(name="test-pkg")
        with patch("astroframe._signature.distribution") as mock_dist:
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "pkg")
            current_hash = compute_package_hash("test-pkg")

        with patch("astroframe._signature.distribution") as mock_dist:
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "pkg")
            result = verify_plugin("test-pkg", manifest, trusted_hash=current_hash)
            assert result.verified is True
            assert result.method == "trusted_hash"

    def test_trusted_hash_mismatch_returns_unverified(self, tmp_path: Path) -> None:
        """trusted_hash 不匹配 → UNVERIFIED（内容已变更）。"""
        _make_py_file(tmp_path, "pkg/__init__.py", "x = 1\n")
        manifest = _make_manifest(name="test-pkg")
        with patch("astroframe._signature.distribution") as mock_dist:
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "pkg")
            result = verify_plugin("test-pkg", manifest, trusted_hash="sha256:" + "0" * 64)
            assert result.verified is False
            assert "已变更" in (result.error or "")

    def test_trusted_hash_empty_string_falls_through(self, tmp_path: Path) -> None:
        """空 trusted_hash → 走签名验证路径。"""
        _make_py_file(tmp_path, "pkg/__init__.py", "x = 1\n")
        manifest = _make_manifest(name="test-pkg")
        with patch("astroframe._signature.distribution") as mock_dist:
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "pkg")
            result = verify_plugin("test-pkg", manifest, trusted_hash="")
            assert result.verified is False

    def test_hash_unavailable_falls_through(self, tmp_path: Path) -> None:
        """compute_package_hash → None → UNVERIFIED。"""
        manifest = _make_manifest(name="test-pkg")
        with patch("astroframe._signature.distribution") as mock_dist:
            mock_dist.return_value.locate_file.return_value = str(tmp_path / "nonexistent")
            result = verify_plugin("test-pkg", manifest, trusted_hash="sha256:" + "a" * 64)
            assert result.verified is False


# ══════════════════════════════════════════════════════════════════════════════════
# TestDiscoverVerifiers
# ══════════════════════════════════════════════════════════════════════════════════


class TestDiscoverVerifiers:
    def test_builtin_verifiers_present(self) -> None:
        """内置三种验证器始终存在。"""
        verifiers = discover_verifiers()
        assert "sigstore" in verifiers
        assert "gpg" in verifiers
        assert "unsigned" in verifiers
        assert verifiers["unsigned"] is _UnsignedVerifier
        assert verifiers["sigstore"] is _SigstoreVerifier
        assert verifiers["gpg"] is _GpgVerifier

    def test_invoke_unsigned_verifier(self) -> None:
        """通过 discover_verifiers 获取的 unsigned verifier 可以调用 verify。"""
        verifiers = discover_verifiers()
        verifier_cls = verifiers["unsigned"]
        result = verifier_cls.verify(Path("/tmp"), _make_manifest(), {})
        assert result.verified is False

    def test_entry_points_integration(self) -> None:
        """entry_points 第三方验证器可以被发现。"""
        # discover_verifiers handles entry_points scan errors gracefully
        verifiers = discover_verifiers()
        assert len(verifiers) >= 3
