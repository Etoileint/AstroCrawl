"""插件包签名验证 — ADR-0011 S17.

sigstore（主线，PEP 740，可选依赖 sigstore-python）+
GPG（兜底，离线/air-gapped 环境）+
unsigned（显式标记，永远返回 UNVERIFIED）。

信任锚：GitHub OIDC → sigstore 证书 → Rekor 透明日志。
最终锚点：granted_hash（SHA256 of package contents）——即使签名被完全绕过，
内容变更也被 hash pin 捕获。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from importlib.metadata import distribution, entry_points
from pathlib import Path
from typing import Any

from astrobase import LogfmtLogger
from astroframe._errors import ManifestValidationError, SignatureError
from astroframe._types import PluginManifest, SignatureResult

log = LogfmtLogger("astroframe.signature")

# ── 包哈希常量 ─────────────────────────────────────────────────────────────────

_HASH_EXCLUDE_DIRS: frozenset[str] = frozenset({"__pycache__", ".git", ".hg", ".svn"})
_HASH_EXCLUDE_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo")
_DIST_INFO_SUFFIXES: tuple[str, ...] = (".dist-info", ".egg-info")
_HASH_MAX_FILES = 5000
_HASH_BUFFER_SIZE = 64 * 1024

_EDITABLE_EXCLUDE_DIRS: frozenset[str] = frozenset({"tests", "docs", ".github", ".tox", ".mypy_cache"})

# ── sigstore identity 格式 ─────────────────────────────────────────────────────

_SIGSTORE_IDENTITY_RE = re.compile(
    r"^https://github\.com/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+/"
    r"\.github/workflows/[a-zA-Z0-9._-]+\.ya?ml@refs/tags/[^/]+$"
)

# ── GPG 常量 ───────────────────────────────────────────────────────────────────

_GPG_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{40}$")
_GPG_TIMEOUT_SECONDS = 30
_GPG_MAX_STDERR_BYTES = 64 * 1024

_GPG_VALIDSIG_RE = re.compile(r"^\[GNUPG:\]\s*VALIDSIG\s+([0-9A-Fa-f]{40})\b", re.MULTILINE)
_GPG_KEY_LINE_RE = re.compile(r"using\s+(?:RSA|EDDSA|ECDSA|DSA)\s+key\s+([0-9A-Fa-f\s]{40,})")
_GPG_PRIMARY_FP_RE = re.compile(r"Primary\s+key\s+fingerprint:\s*([0-9A-Fa-f\s]{40,})")


# ══════════════════════════════════════════════════════════════════════════════════
# 包哈希计算
# ══════════════════════════════════════════════════════════════════════════════════


def compute_package_hash(package_name: str) -> str | None:
    """计算已安装包的确定性 SHA256 哈希。

    递归收集包目录中所有源代码文件（排除 __pycache__, .pyc, .dist-info,
    .egg-info, 隐藏目录等），按路径排序后逐文件 SHA256 → 拼接 → 最终 SHA256。
    符号链接不跟随。

    Returns:
        "sha256:<hex>" 或 None（无法定位包 / 空文件列表 / 文件数超限 / 文件不可读）。
    """
    dist_root = _resolve_dist_root(package_name)
    if dist_root is None:
        return None

    if not dist_root.is_dir():
        log.debug("plugin_hash_not_directory", package=package_name, path=str(dist_root))
        return None

    is_editable = _check_editable_install(package_name, dist_root)
    if is_editable:
        log.debug("plugin_hash_editable_install", package=package_name)

    files = _collect_hashable_files(dist_root, is_editable)
    if files is None:
        return None

    if not files:
        log.warning("plugin_hash_empty_file_list", package=package_name)
        return None

    # 按相对路径排序以保证确定性
    files.sort(key=lambda p: p[0].as_posix())

    hasher = hashlib.sha256()
    for _rel_path, full_path in files:
        file_hash = _hash_single_file(full_path)
        if file_hash is None:
            return None
        hasher.update(file_hash.encode("ascii"))
        hasher.update(b":")

    final_hex = hasher.hexdigest()
    return f"sha256:{final_hex}"


def _resolve_dist_root(package_name: str) -> Path | None:
    """定位已安装包的根目录。失败返回 None。"""
    try:
        dist = distribution(package_name)
    except Exception:
        log.debug("plugin_hash_distribution_not_found", package=package_name)
        return None

    try:
        root = dist.locate_file("")
    except Exception:
        return None

    if root is None:
        return None

    return Path(str(root)).resolve()


def _check_editable_install(package_name: str, dist_root: Path) -> bool:
    """检测是否为 editable install（pip install -e）。"""
    try:
        dist = distribution(package_name)
        direct_url_raw = dist.read_text("direct_url.json")
        if direct_url_raw is None:
            return False
        direct_url = json.loads(direct_url_raw)
        return bool(direct_url.get("dir_info", {}).get("editable", False))
    except Exception:
        return False


def _collect_hashable_files(dist_root: Path, is_editable: bool) -> list[tuple[Path, Path]] | None:
    """递归收集所有应参与哈希计算的文件。

    Returns:
        [(relative_path, full_path), ...] 或 None（文件数超限）。
    """
    files: list[tuple[Path, Path]] = []
    exclude_dirs = _HASH_EXCLUDE_DIRS
    if is_editable:
        exclude_dirs = _HASH_EXCLUDE_DIRS | _EDITABLE_EXCLUDE_DIRS

    try:
        for dirpath_str, dirnames, filenames in os.walk(dist_root):
            dirpath = Path(dirpath_str)
            dirnames[:] = [
                d
                for d in dirnames
                if d not in exclude_dirs and not d.startswith(".") and not d.endswith(_DIST_INFO_SUFFIXES)
            ]

            for fname in filenames:
                if fname.startswith("."):
                    continue
                file_path = dirpath / fname
                if file_path.name.endswith(_HASH_EXCLUDE_SUFFIXES):
                    continue
                if file_path.is_symlink():
                    log.debug("plugin_hash_symlink_skipped", path=str(file_path))
                    continue
                if len(files) >= _HASH_MAX_FILES:
                    log.error(
                        "plugin_hash_too_many_files",
                        package=str(dist_root),
                        limit=_HASH_MAX_FILES,
                    )
                    return None
                relative = file_path.relative_to(dist_root)
                files.append((relative, file_path))
    except OSError as exc:
        log.warning("plugin_hash_walk_error", path=str(dist_root), error=str(exc))
        return None

    return files


def _hash_single_file(file_path: Path) -> str | None:
    """计算单个文件的 SHA256 hex digest（64KB 分块读取）。

    Returns:
        hex digest 或 None（文件不可读）。
    """
    hasher = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(_HASH_BUFFER_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError as exc:
        log.warning("plugin_hash_file_unreadable", path=str(file_path), error=str(exc))
        return None
    return hasher.hexdigest()


# ══════════════════════════════════════════════════════════════════════════════════
# manifest signing 字段格式校验
# ══════════════════════════════════════════════════════════════════════════════════


def validate_signing_field(signing: Any) -> None:
    """校验 manifest signing 字段格式（manifest 加载前调用，零 import）。

    规则：
      - signing is None → 合法（无签名 = 默认 unsigned）
      - 非 dict 且非 None → ManifestValidationError
      - method 不在已知集合 → ManifestValidationError（→ INCOMPATIBLE）
      - sigstore 缺少 identity 或格式不匹配 → ManifestValidationError
      - gpg 缺少 key_fingerprint 或格式不匹配 → ManifestValidationError

    Raises:
        ManifestValidationError: signing 格式不合法。
    """
    if signing is None:
        return

    if not isinstance(signing, dict):
        raise ManifestValidationError("signing 必须是对象或 null")

    method = signing.get("method")
    if not method:
        raise ManifestValidationError("signing.method 为必填字段")

    if not isinstance(method, str):
        raise ManifestValidationError(f"signing.method 必须是字符串，实际为 {type(method).__name__}")

    if method not in ("sigstore", "gpg", "unsigned"):
        raise ManifestValidationError(f"signing.method '{method}' 不被支持。合法值: sigstore, gpg, unsigned")

    if method == "sigstore":
        _validate_sigstore_signing(signing)
    elif method == "gpg":
        _validate_gpg_signing(signing)
    elif method == "unsigned":
        if "identity" in signing:
            log.debug("plugin_signing_unsigned_extra_key", key="identity")
        if "key_fingerprint" in signing:
            log.debug("plugin_signing_unsigned_extra_key", key="key_fingerprint")


def _validate_sigstore_signing(signing: dict[str, Any]) -> None:
    """校验 sigstore signing 段的 identity 字段。"""
    identity = signing.get("identity")
    if not identity:
        raise ManifestValidationError("signing.method='sigstore' 需要 'identity' 字段（OIDC Subject URI）")

    if not isinstance(identity, str):
        raise ManifestValidationError(f"signing.identity 必须是字符串，实际为 {type(identity).__name__}")

    if len(identity) > 512:
        raise ManifestValidationError(f"signing.identity 长度 {len(identity)} 超过上限 512")

    if not _SIGSTORE_IDENTITY_RE.match(identity):
        raise ManifestValidationError(
            f"signing.identity 格式不合法: '{identity}'。"
            f"期望格式: https://github.com/<owner>/<repo>/.github/workflows/<workflow>.yml@refs/tags/<tag>"
        )


def _validate_gpg_signing(signing: dict[str, Any]) -> None:
    """校验 GPG signing 段的 key_fingerprint 字段。"""
    fp_raw = signing.get("key_fingerprint")
    if not fp_raw:
        raise ManifestValidationError("signing.method='gpg' 需要 'key_fingerprint' 字段（40 字符 hex）")

    if not isinstance(fp_raw, str):
        raise ManifestValidationError(f"signing.key_fingerprint 必须是字符串，实际为 {type(fp_raw).__name__}")

    if len(fp_raw) > 80:
        raise ManifestValidationError(f"signing.key_fingerprint 长度 {len(fp_raw)} 超过上限 80（40 hex + 空格）")

    cleaned = re.sub(r"\s+", "", fp_raw).lower()
    if not _GPG_FINGERPRINT_RE.match(cleaned):
        raise ManifestValidationError(f"signing.key_fingerprint 格式不合法: '{fp_raw}'。期望 40 字符 hex 字符串")


# ══════════════════════════════════════════════════════════════════════════════════
# 签名验证主入口
# ══════════════════════════════════════════════════════════════════════════════════


def verify_plugin(
    package_name: str,
    manifest: PluginManifest,
    *,
    trusted_hash: str | None = None,
) -> SignatureResult:
    """插件签名验证主入口。

    流程：
      1. 若 trusted_hash 非空且匹配当前包哈希 → 直接返回 VERIFIED（hash pin 快速路径）
      2. 若 trusted_hash 非空但不匹配 → 返回 UNVERIFIED（内容已变更）
      3. 若 trusted_hash 为 None → 走完整签名验证路径

    Args:
        package_name: 已安装包名。
        manifest: 插件 manifest。
        trusted_hash: 已信任的包哈希（来自 trust record）。None 表示首次验证。

    Returns:
        SignatureResult。verified=True 仅当签名数学验证通过 + 身份匹配 + Rekor 可审计。

    Raises:
        SignatureError: 基础设施故障（sigstore 库缺失、网络不可达、gpg CLI 缺失等）。
    """
    signing = manifest.signing
    signing_dict: dict[str, Any] = signing if isinstance(signing, dict) else {}
    method = signing_dict.get("method", "unsigned")

    # ── 快速路径：哈希锁定 ────────────────────────────────────────────────────
    if trusted_hash is not None and trusted_hash.strip():
        hash_current = compute_package_hash(package_name)
        if hash_current is None:
            log.warning(
                "plugin_sig_hash_unavailable",
                package=package_name,
            )
            return SignatureResult.unverified("无法计算包哈希，无法验证哈希锁定")
        if trusted_hash == hash_current:
            log.debug("plugin_sig_hash_match", package=package_name)
            return SignatureResult(verified=True, method="trusted_hash", identity="hash pin")

        log.info(
            "plugin_sig_hash_changed",
            package=package_name,
            trusted=trusted_hash[:16],
            current=hash_current[:16],
        )
        return SignatureResult.unverified(
            f"包内容哈希已变更 (trusted={trusted_hash[:16]}..., current={hash_current[:16]}...)"
        )

    # ── 签名验证路径 ──────────────────────────────────────────────────────────
    verifiers = discover_verifiers()
    verifier_cls = verifiers.get(method)
    if verifier_cls is None:
        return SignatureResult.unverified(f"unknown signing method: {method}")

    dist_path = _resolve_dist_path(package_name)

    # TOCTOU 防护：验证前拍 hash
    hash_before = compute_package_hash(package_name)

    try:
        result: SignatureResult = verifier_cls.verify(dist_path, manifest, signing_dict)  # type: ignore[attr-defined]
    except SignatureError:
        raise
    except Exception as exc:
        log.error("plugin_sig_unexpected_error", package=package_name, error=str(exc))
        return SignatureResult.unverified(f"unexpected verification error: {exc}")

    # TOCTOU 防护：验证后 hash 比对
    hash_after = compute_package_hash(package_name)
    if hash_before is not None and hash_after is not None and hash_before != hash_after:
        log.error("plugin_sig_toctou_detected", package=package_name)
        return SignatureResult.unverified(
            "包内容在签名验证期间被并发修改——请重试验证。若反复出现，检查是否有并发 pip install 操作。"
        )

    return result


def _resolve_dist_path(package_name: str) -> Path:
    """定位已安装包的根目录。失败抛 SignatureError。"""
    dist_root = _resolve_dist_root(package_name)
    if dist_root is None:
        raise SignatureError(f"无法定位已安装包 '{package_name}'")
    return dist_root


# ══════════════════════════════════════════════════════════════════════════════════
# Unsigned Verifier
# ══════════════════════════════════════════════════════════════════════════════════


class _UnsignedVerifier:
    """S17 unsigned 桩——永远返回 UNVERIFIED。零依赖，零 I/O。"""

    @staticmethod
    def verify(dist_path: Path, manifest: PluginManifest, signing: dict[str, Any]) -> SignatureResult:
        return SignatureResult.unverified("plugin has no signature (method: unsigned)")


# ══════════════════════════════════════════════════════════════════════════════════
# Sigstore Verifier
# ══════════════════════════════════════════════════════════════════════════════════


class _SigstoreVerifier:
    """S17 sigstore 验证器——PEP 740 OIDC 证书 + Rekor 透明日志。

    验证链（ADR-0011 S17）：
      a. 数学签名正确 → 签名者持有私钥
      b. OIDC Subject 匹配 manifest.signing.identity → 来自正确的仓库
      c. 证书记录在 Rekor 透明日志中 → 公开可审计，不可篡改
      d. 内容 hash 由 granted_hash 锁定 + TOCTOU 双 hash 防护
    """

    @staticmethod
    def verify(dist_path: Path, manifest: PluginManifest, signing: dict[str, Any]) -> SignatureResult:
        try:
            from sigstore.verify import verify as sigstore_verify  # type: ignore[import-untyped]
        except ImportError:
            raise SignatureError(
                "sigstore-python 未安装。签名验证需要 sigstore 支持。"
                "安装: pip install astroframe[sigstore]  或  pip install sigstore"
            ) from None

        identity: str = signing["identity"]

        wheel_path = _find_wheel_in_pip_cache(manifest.name)
        if wheel_path is None:
            return SignatureResult.unverified(
                "已安装包无原始 wheel 文件。sigstore 验证需要构建时的 wheel。"
                "首次安装推荐 pip install --require-hashes <package> 建立信任锚点。"
                "后续信任通过 granted_hash 哈希锁定保障内容完整性。"
            )

        if not wheel_path.is_file():
            return SignatureResult.unverified(
                "sigstore: wheel 文件在验证前被移除（并发 pip cache purge？）。请重试验证。"
            )

        try:
            result = sigstore_verify(wheel_path)
        except FileNotFoundError:
            return SignatureResult.unverified("sigstore: wheel 文件在签名验证期间被移除（并发操作冲突）")
        except Exception as exc:
            log.warning("plugin_sigstore_verify_error", package=manifest.name, error=str(exc))
            return SignatureResult.unverified(f"sigstore verification failed: {exc}")

        if not _get_sigstore_verified(result):
            return SignatureResult.unverified("sigstore: 签名数学验证未通过")

        cert_identity = _get_sigstore_cert_identity(result)
        if cert_identity is None:
            return SignatureResult.unverified("sigstore: 无法从证书中提取 OIDC Subject")
        if not _match_sigstore_identity(cert_identity, identity):
            return SignatureResult.unverified(
                f"sigstore: 证书 OIDC Subject 不匹配 manifest 声明的 identity。"
                f"证书声称: {cert_identity}；manifest 声明: {identity}"
            )

        rekor_result = _check_rekor_entry(result)
        if rekor_result == "network_error":
            raise SignatureError(
                "sigstore: 无法连接 Rekor 透明日志服务器——网络不可达。"
                "请检查网络连接后重试，或使用 GPG 签名（method: gpg）进行离线验证。"
            )
        if rekor_result == "not_found":
            return SignatureResult.unverified("sigstore: 签名未在 Rekor 透明日志中记录——不可审计，拒绝信任")

        return SignatureResult(verified=True, method="sigstore", identity=identity)


def _get_sigstore_verified(result: Any) -> bool:
    """从 sigstore-python VerificationResult 提取验证状态。兼容 1.x 和 0.3.x API。"""
    if hasattr(result, "success"):
        return bool(result.success)
    if hasattr(result, "verified"):
        return bool(result.verified)
    log.error("plugin_sigstore_unknown_api", result_type=type(result).__name__)
    return False


def _get_sigstore_cert_identity(result: Any) -> str | None:
    """从 VerificationResult 提取 OIDC 证书 Subject。兼容多版本 API。"""
    cert = None
    if hasattr(result, "cert"):
        cert = result.cert
    elif hasattr(result, "certificate"):
        cert = result.certificate

    if cert is None:
        return None

    if hasattr(cert, "san") and cert.san is not None:
        return str(cert.san)
    if hasattr(cert, "identity") and hasattr(cert, "oidc_issuer"):
        return str(cert.identity)

    return None


def _match_sigstore_identity(actual: str, expected: str) -> bool:
    """匹配 OIDC Subject——精确比对，两端去空白。"""
    return actual.strip() == expected.strip()


def _check_rekor_entry(result: Any) -> str:
    """检查 sigstore 验证结果中的 Rekor 透明日志条目。

    Returns:
        "verified": Rekor 条目存在，可审计
        "not_found": 无 Rekor 条目
        "network_error": 网络异常（需向上传播为 SignatureError）
    """
    try:
        if hasattr(result, "log_entry") and result.log_entry is not None:
            if hasattr(result.log_entry, "log_index"):
                return "verified"
            return "not_found"

        if hasattr(result, "bundle") and result.bundle is not None:
            bundle = result.bundle
            if hasattr(bundle, "rekor_entry") and bundle.rekor_entry is not None:
                return "verified"
            return "not_found"

        return "not_found"
    except Exception as exc:
        log.warning("plugin_sigstore_rekor_check_error", error=str(exc))
        return "network_error"


# ══════════════════════════════════════════════════════════════════════════════════
# GPG Verifier
# ══════════════════════════════════════════════════════════════════════════════════


class _GpgVerifier:
    """S17 GPG 验证器——离线/air-gapped 环境兜底。

    验证链：
      1. gpg CLI 在 PATH 上可用 → 否则 SignatureError（环境问题，非信任问题）
      2. 在 pip cache 或包目录中发现 GPG detached signature（.asc 或 .sig）
      3. 在 pip cache 中发现原始 wheel/sdist（detached signature 需要数据文件）
      4. gpg --verify <sig_file> <data_file> 签名数学验证通过
      5. 签名 key fingerprint 匹配 manifest.signing.key_fingerprint
    """

    @staticmethod
    def verify(dist_path: Path, manifest: PluginManifest, signing: dict[str, Any]) -> SignatureResult:
        gpg_bin = shutil.which("gpg")
        if gpg_bin is None:
            gpg_bin = shutil.which("gpg2")
        if gpg_bin is None:
            raise SignatureError(
                "gpg CLI 未在 PATH 中找到。"
                "Debian/Ubuntu: apt install gnupg  |  macOS: brew install gnupg  |  "
                "Windows: winget install GnuPG.GnuPG"
            )

        key_fingerprint_raw: str = signing["key_fingerprint"]
        cleaned = re.sub(r"\s+", "", key_fingerprint_raw).lower()
        if not _GPG_FINGERPRINT_RE.match(cleaned):
            return SignatureResult.unverified(f"gpg: key_fingerprint 格式非法 '{key_fingerprint_raw}'")

        sig_path, data_path = _find_gpg_signature_and_data(dist_path, manifest.name)
        if sig_path is None:
            return SignatureResult.unverified(
                "gpg: 未找到 GPG detached signature 文件（.asc / .sig）。"
                "期望位置: pip cache 中 wheel 旁，或包 .dist-info/ 目录"
            )
        if data_path is None:
            return SignatureResult.unverified(
                "gpg: 找到签名文件但无法获取被签名的原始 wheel/sdist 文件。"
                "GPG detached signature 需要原始分发文件进行验证。"
                "请确保 pip cache 未被清除，或使用 sigstore 签名方式。"
            )

        if not sig_path.is_file() or not data_path.is_file():
            return SignatureResult.unverified("gpg: 签名文件或数据文件在验证前被移除（并发 pip cache purge？）")

        gpg_env = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": os.environ.get("PATH", "")}
        try:
            proc = subprocess.run(
                [gpg_bin, "--verify", "--status-fd", "1", str(sig_path), str(data_path)],
                capture_output=True,
                timeout=_GPG_TIMEOUT_SECONDS,
                text=True,
                env=gpg_env,
            )
        except subprocess.TimeoutExpired:
            raise SignatureError("gpg verification timed out after 30s") from None
        except OSError as exc:
            raise SignatureError(f"gpg verification OS error: {exc}") from exc
        except FileNotFoundError:
            return SignatureResult.unverified("gpg: 签名文件或数据文件在验证启动时已被移除（并发 pip cache purge？）")

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        if len(stderr.encode("utf-8", errors="replace")) > _GPG_MAX_STDERR_BYTES:
            stderr = stderr[: _GPG_MAX_STDERR_BYTES // 2]

        if proc.returncode != 0:
            return SignatureResult.unverified(f"gpg: 签名验证失败 (exit={proc.returncode})")

        signing_fp = _extract_gpg_validsig_fingerprint(stdout)
        if signing_fp is None:
            signing_fp = _extract_gpg_signing_fingerprint(stderr)
        if signing_fp is None:
            return SignatureResult.unverified("gpg: 签名数学验证通过，但无法从签名中提取 key fingerprint")

        if signing_fp != cleaned:
            return SignatureResult.unverified(
                f"gpg: key fingerprint 不匹配。签名者: {signing_fp}；manifest 声明: {cleaned}"
            )

        return SignatureResult(verified=True, method="gpg", identity=f"gpg:{signing_fp}")


def _extract_gpg_validsig_fingerprint(stdout: str) -> str | None:
    """从 GPG --status-fd 1 的 VALIDSIG 行提取 fingerprint（G4 主策略）。

    VALIDSIG 格式:
      [GNUPG:] VALIDSIG <fingerprint_hex_40> <sig_date> <sig_ts> <expire_ts> <keyid> ...

    提取第一组 40 字符 hex fingerprint，去空格，小写化返回。未匹配 → None。
    """
    m = _GPG_VALIDSIG_RE.search(stdout)
    if m is None:
        return None
    return m.group(1).lower()


def _extract_gpg_signing_fingerprint(stderr: str) -> str | None:
    """从 gpg --verify stderr 输出中提取签名 key fingerprint（回退策略）。

    兼容多版本 GPG 输出格式：
      1. "using (RSA|EDDSA|ECDSA|DSA) key <40-hex-fingerprint>"
      2. "Primary key fingerprint: <40-hex-fingerprint>"
    """
    m = _GPG_KEY_LINE_RE.search(stderr)
    if m is not None:
        fp = m.group(1)
        cleaned = re.sub(r"\s+", "", fp).lower()
        if len(cleaned) >= 40:
            return cleaned[:40]
        return None

    m = _GPG_PRIMARY_FP_RE.search(stderr)
    if m is not None:
        fp = m.group(1)
        cleaned = re.sub(r"\s+", "", fp).lower()
        if len(cleaned) >= 40:
            return cleaned[:40]
        return None

    return None


# ══════════════════════════════════════════════════════════════════════════════════
# GPG 辅助：签名文件及数据文件发现
# ══════════════════════════════════════════════════════════════════════════════════


def _find_gpg_signature_and_data(dist_path: Path, package_name: str) -> tuple[Path | None, Path | None]:
    """查找 GPG detached signature 及对应的数据文件。

    Returns:
        (signature_path, data_path)。两者均可能为 None。

    策略（按优先级）：
      1. pip cache：找到 wheel/sdist → 同名 + .asc/.sig
      2. 包目录 .dist-info/：*.asc → pip cache 中找对应 wheel
      3. 包根目录：*.asc + pip cache 对应 wheel
      4. 包目录 .dist-info/：*.sig → 同上
      5. 全部失败 → (None, None)
    """
    wheel = _find_wheel_in_pip_cache(package_name)

    extensions = [".asc", ".sig"]
    for ext in extensions:
        if wheel is not None:
            sig = wheel.with_suffix(wheel.suffix + ext)
            if sig.is_file():
                return (sig, wheel)

        for dist_info in dist_path.glob("*.dist-info"):
            for sig_file in dist_info.glob(f"*{ext}"):
                return (sig_file, wheel)

        for sig_file in dist_path.glob(f"*{ext}"):
            return (sig_file, wheel)

    return (None, None)


# ══════════════════════════════════════════════════════════════════════════════════
# pip cache wheel 发现
# ══════════════════════════════════════════════════════════════════════════════════


def _find_wheel_in_pip_cache(package_name: str) -> Path | None:
    """在 pip cache 中查找指定包的 wheel 文件。多策略回退。

    策略 1: pip cache list（推荐）
    策略 2: pip cache dir + glob
    策略 3: RECORD 文件提取 → 策略 2 查找
    """
    wheel = _find_wheel_via_cache_list(package_name)
    if wheel is not None:
        return wheel

    cache_dir = _get_pip_cache_dir()
    if cache_dir is not None:
        wheel = _find_wheel_in_cache_dir(cache_dir, package_name)
        if wheel is not None:
            return wheel

    wheel = _find_wheel_via_record(package_name, cache_dir)
    return wheel


def _find_wheel_via_cache_list(package_name: str) -> Path | None:
    """通过 'pip cache list' 查找 wheel。"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "cache", "list", package_name],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    for line in result.stdout.splitlines():
        line = line.strip()
        if line.endswith(".whl") and package_name.lower().replace("-", "_") in line.lower():
            p = Path(line)
            if p.is_file():
                return p

    return None


def _get_pip_cache_dir() -> Path | None:
    """获取 pip cache 目录路径。"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "cache", "dir"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    cache_dir = result.stdout.strip()
    if cache_dir:
        p = Path(cache_dir)
        if p.is_dir():
            return p
    return None


def _find_wheel_in_cache_dir(cache_dir: Path, package_name: str) -> Path | None:
    """在 pip cache 目录中 glob 查找 wheel。"""
    wheels_dir = cache_dir / "wheels"
    if not wheels_dir.is_dir():
        return None

    candidate_prefix = package_name.lower().replace("-", "_")
    candidates: list[Path] = []
    try:
        for whl in wheels_dir.rglob("*.whl"):
            if whl.is_file() and candidate_prefix in whl.name.lower():
                candidates.append(whl)
    except OSError:
        return None

    if not candidates:
        return None

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _find_wheel_via_record(package_name: str, cache_dir: Path | None) -> Path | None:
    """通过 .dist-info/RECORD 提取 wheel 名，再到 cache 查找。"""
    dist_root = _resolve_dist_root(package_name)
    if dist_root is None:
        return None

    dist_infos = list(dist_root.glob("*.dist-info"))
    if not dist_infos:
        return None

    for di in dist_infos:
        record = di / "RECORD"
        if not record.is_file():
            continue
        try:
            first_line = record.read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, UnicodeDecodeError):
            continue
        parts = first_line.split(",")[0].strip()
        whl_name = Path(parts).name
        if not whl_name.endswith(".whl"):
            continue

        if cache_dir is not None:
            for whl in cache_dir.rglob(f"*{whl_name}*"):
                if whl.is_file():
                    return whl
        break

    return None


# ══════════════════════════════════════════════════════════════════════════════════
# 验证器发现（含 entry_points 扩展性）
# ══════════════════════════════════════════════════════════════════════════════════


def discover_verifiers() -> dict[str, type]:
    """发现所有签名验证器。

    内置三种（sigstore, gpg, unsigned）始终可用。
    第三方验证器通过 entry_points 注册，可覆盖内置实现。

    entry_points 格式（对标 ADR-0006 AI Provider）：
      [project.entry-points."astroframe.signature_verifiers"]
      "my-verifier" = "my_package.verifier:MyVerifier"

    value 指向实现 verify() static method 的类。
    ep.load() 失败 → log warning + 不覆盖内置。
    """
    verifiers: dict[str, type] = {
        "sigstore": _SigstoreVerifier,
        "gpg": _GpgVerifier,
        "unsigned": _UnsignedVerifier,
    }

    try:
        for ep in entry_points(group="astroframe.signature_verifiers"):
            try:
                verifier_cls = ep.load()
                if hasattr(verifier_cls, "verify") and callable(verifier_cls.verify):
                    verifiers[ep.name] = verifier_cls
                else:
                    log.warning(
                        "plugin_sig_verifier_no_verify_method",
                        entry_point=ep.name,
                    )
            except Exception as exc:
                log.warning(
                    "plugin_sig_verifier_entry_point_failed",
                    entry_point=ep.name,
                    error=str(exc),
                )
    except Exception as exc:
        log.warning("plugin_sig_verifier_scan_failed", error=str(exc))

    return verifiers
