"""远程规则源管理 — Manifest 下载 + 规则按需下载 + 源生命周期。

网络层 10 项安全措施 (S01-S10) + 冲突更新层 6 项 (S35-S39+N14)。
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, cast
from urllib.parse import urlparse

if sys.platform != "win32":
    import fcntl
else:
    fcntl = None  # type: ignore[assignment]

import aiohttp

from astrobasis import LogfmtLogger, atomic_write_json
from astrocrawl._constants import (
    DOWNLOAD_CONCURRENCY_GLOBAL,
    DOWNLOAD_CONCURRENCY_PER_SOURCE,
    MANIFEST_MAX_BYTES,
    MAX_REDIRECTS,
    MAX_RULE_FILE_SIZE,
    MAX_SOURCE_URL_LENGTH,
    SOURCE_DAILY_UPDATE_LIMIT,
    SOURCE_DEGRADED_COOLDOWN,
    SOURCE_DOWNLOAD_TIMEOUT,
)
from astrocrawl.rules._io import safe_write_rule_file

logger = LogfmtLogger("astrocrawl.rules.source")

SOURCES_FILE = Path.home() / ".astrocrawl" / "sources.json"
_SOURCES_LOCK_SUFFIX = ".lock"


def _acquire_sources_lock(exclusive: bool = True) -> int | None:
    """获取 sources.json 的 fcntl.flock 锁。Windows 上退化为无锁。"""
    if fcntl is None:
        return None
    lock_file = SOURCES_FILE.with_suffix(SOURCES_FILE.suffix + _SOURCES_LOCK_SUFFIX)
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        return fd
    except OSError:
        logger.warning("sources_lock_acquire_failed", path=lock_file)
        return None


def _release_sources_lock(fd: int | None) -> None:
    if fd is None or fcntl is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except OSError:
        pass


# ── 私有/环回/链路本地 IP 段 (S05) ──
_PRIVATE_NETS = [
    ipaddress.ip_network("0.0.0.0/8"),  # "This" Network (RFC 1122)
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT (RFC 6598)
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),  # Benchmark (RFC 2544)
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::ffff:0:0/96"),  # IPv4-mapped IPv6
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# S07: URL 含 @ 认证信息检测
_AUTH_DETECT_PREFIXES = ("https://", "http://")


def _log_safe_url(url: str) -> str:
    """N13: 日志 URL 脱敏 — 仅保留 scheme://netloc，防止 S3 预签名 token 泄漏。"""
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return url[:64]


# ── sources.json 文件级 CRUD（CLI 可直接使用，无需 SourceManager 实例）──


def list_sources_from_file() -> List[Dict[str, Any]]:
    """读取 sources.json 中的源列表。文件不存在时返回空列表。"""
    if not SOURCES_FILE.is_file():
        return []
    try:
        raw = SOURCES_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and isinstance(data.get("sources"), list):
            return cast("list[dict[str, Any]]", data["sources"])
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        logger.warning("sources_file_corrupt", error=exc)
    return []


def add_source_to_file(name: str, url: str, **kwargs) -> Dict[str, Any]:
    """添加源到 sources.json。LOCK_EX 包裹 RMW 周期。返回添加的源条目。"""
    url = _validate_source_url(url)
    lock_fd = _acquire_sources_lock(exclusive=True)
    try:
        sources = list_sources_from_file()
        for s in sources:
            if s.get("name") == name:
                raise ValueError(f"源 '{name}' 已存在")
        entry = {"name": name, "url": url}
        entry.update(kwargs)
        sources.append(entry)
        _write_sources_file(sources)
    finally:
        _release_sources_lock(lock_fd)
    logger.info("source_added", name=name, url=_log_safe_url(url))
    return entry


def remove_source_from_file(name: str) -> bool:
    """从 sources.json 移除源。LOCK_EX 包裹 RMW 周期。返回是否实际移除。"""
    lock_fd = _acquire_sources_lock(exclusive=True)
    try:
        sources = list_sources_from_file()
        new_sources = [s for s in sources if s.get("name") != name]
        if len(new_sources) == len(sources):
            return False
        _write_sources_file(new_sources)
    finally:
        _release_sources_lock(lock_fd)
    logger.info("source_removed", name=name)
    return True


def get_source_from_file(name: str) -> Optional[Dict[str, Any]]:
    """从 sources.json 获取单个源。"""
    for s in list_sources_from_file():
        if s.get("name") == name:
            return s
    return None


def update_source_in_file(name: str, **meta) -> bool:
    """更新 sources.json 中某源的元数据字段。LOCK_EX 包裹 RMW 周期。返回是否找到并更新。"""
    lock_fd = _acquire_sources_lock(exclusive=True)
    try:
        sources = list_sources_from_file()
        for s in sources:
            if s.get("name") == name:
                s.update(meta)
                _write_sources_file(sources)
                return True
    finally:
        _release_sources_lock(lock_fd)
    return False


def _write_sources_file(sources: List[Dict[str, Any]]) -> None:
    """原子写入 sources.json。"""
    atomic_write_json(SOURCES_FILE, {"sources": sources})


# ── SourceState ────────────────────────────────────────


@dataclass
class SourceState:
    """远程源的运行时状态。"""

    name: str = ""
    url: str = ""
    title: str = ""
    maintainer: str = ""
    homepage: str = ""
    last_updated: float = 0.0
    last_manifest_hash: str = ""
    daily_update_count: int = 0
    daily_update_date: str = ""
    consecutive_failures: int = 0
    state: str = "active"  # active | degraded | emergency_disabled | moved | offline
    degraded_at: float = 0.0  # M2: time.monotonic() when entering degraded
    moved_to: str = ""
    rules_count: int = 0


class SourceManager:
    """远程规则源管理器。

    负责 Manifest 下载/校验 + 规则按需下载 + 源生命周期管理。
    """

    def __init__(
        self, session: aiohttp.ClientSession, cache_dir: Path, auto_update: bool = True, proxy_url: str | None = None
    ) -> None:
        self._session = session
        self._cache_dir = cache_dir
        self._auto_update = auto_update
        self._proxy_url = proxy_url
        self._sources: Dict[str, SourceState] = {}
        self._global_sem = asyncio.Semaphore(DOWNLOAD_CONCURRENCY_GLOBAL)
        self._per_source_sems: Dict[str, asyncio.Semaphore] = {}

        # 从 sources.json 恢复持久化源
        for entry in list_sources_from_file():
            try:
                self._add_source_internal(
                    entry["name"], entry["url"], **{k: v for k, v in entry.items() if k not in ("name", "url")}
                )
            except Exception as exc:
                logger.warning("source_load_failed", name=entry.get("name", ""), error=exc)

        self._last_request: float = 0.0

    # ── 源管理 ────────────────────────────────────────

    def add_source(self, name: str, url: str, **kwargs) -> SourceState:
        """添加远程源并持久化到 sources.json。"""
        entry = add_source_to_file(name, url, **kwargs)
        return self._add_source_internal(name, entry["url"], **kwargs)

    def _add_source_internal(self, name: str, url: str, **kwargs) -> SourceState:
        """添加远程源（不持久化，供构造器和测试使用）。"""
        url = _validate_source_url(url)
        state = SourceState(name=name, url=url, **kwargs)
        self._sources[name] = state
        self._per_source_sems[name] = asyncio.Semaphore(DOWNLOAD_CONCURRENCY_PER_SOURCE)
        return state

    def remove_source(self, name: str) -> None:
        """移除远程源，清理缓存目录并更新 sources.json (S44)。"""
        self._sources.pop(name, None)
        self._per_source_sems.pop(name, None)
        source_dir = self._cache_dir / name
        if source_dir.is_dir():
            import shutil

            try:
                shutil.rmtree(source_dir)
            except OSError as exc:
                logger.warning("source_cache_cleanup_failed", name=name, error=exc)
        remove_source_from_file(name)
        logger.info("source_removed", name=name)

    def get_source(self, name: str) -> Optional[SourceState]:
        return self._sources.get(name)

    def list_sources(self) -> List[SourceState]:
        return list(self._sources.values())

    # ── Manifest 下载 ─────────────────────────────────

    async def fetch_manifest(self, source_name: str) -> Dict[str, Any]:
        """下载并校验 manifest (S01-S10)。失败时抛出异常。"""
        source = self._sources.get(source_name)
        if not source:
            raise ValueError(f"源不存在: {source_name}")

        if source.state == "emergency_disabled":
            raise ValueError(f"源已紧急禁用: {source_name}")
        if source.state == "moved":
            raise ValueError(f"源已迁移至 {source.moved_to}, 请更新 URL")

        async with self._global_sem:
            sem = self._per_source_sems.get(source_name)
            if sem:
                async with sem:
                    return await self._do_fetch_manifest(source)

            return await self._do_fetch_manifest(source)

    async def _do_fetch_manifest(self, source: SourceState) -> Dict[str, Any]:
        # S38: 请求间隔 ≥100ms
        await self._rate_limit()

        url = source.url

        # S05: DNS 重绑定防护 — 硬阻断
        await check_dns_rebinding(url)

        try:
            # S01+S02+S08: HTTPS + 证书 + 超时
            async with self._session.get(
                url,
                proxy=self._proxy_url,
                timeout=aiohttp.ClientTimeout(total=SOURCE_DOWNLOAD_TIMEOUT),
                max_redirects=MAX_REDIRECTS,  # S03
                allow_redirects=True,
            ) as resp:
                # S03: 重定向后 URL 不跨源
                _check_redirect_not_cross_origin(str(resp.url), url)

                if resp.status != 200:
                    raise ValueError(f"HTTP {resp.status}")

                # S09: Content-Type
                ct = resp.headers.get("Content-Type", "")
                if "application/json" not in ct and "text/json" not in ct:
                    raise ValueError(f"非 JSON Content-Type: {ct}")

                # S10: 大小限制
                raw_bytes = await resp.read()
                if len(raw_bytes) > MANIFEST_MAX_BYTES:
                    raise ValueError(f"Manifest 超过 {MANIFEST_MAX_BYTES} 字节限制")

                data = json.loads(raw_bytes.decode("utf-8"))

        except aiohttp.ClientError as e:
            raise ValueError(f"下载失败: {e}") from e

        # Manifest schema_version 检查 (N81 + N15)
        sv = data.get("schema_version", 1)
        if not isinstance(sv, int) or sv < 1 or sv > 2:
            logger.warning("manifest_schema_version_skip", source=source.name, version=sv)
            raise ValueError(f"不支持的 manifest schema_version: {sv}")

        # N8: emergency_disable — N16: 提前退出避免继续处理 rules 列表
        if data.get("emergency_disable"):
            source.state = "emergency_disabled"
            logger.warning("source_emergency_disable", source=source.name)
            return cast("dict[str, Any]", data)

        # N83: moved_to
        moved = data.get("moved_to")
        if moved and isinstance(moved, str) and moved.strip():
            source.moved_to = moved
            source.state = "moved"
            logger.info("source_moved", source=source.name, moved_to=moved)

        # 更新状态
        source.last_updated = time.time()
        source.rules_count = len(data.get("rules", []))
        source.title = data.get("title", source.title)
        source.maintainer = data.get("maintainer", source.maintainer)
        source.homepage = data.get("homepage", source.homepage)

        # 记录 manifest hash 用于后续 diff
        source.last_manifest_hash = hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

        # N43: 重置连续失败计数
        source.consecutive_failures = 0
        if source.state == "degraded":
            source.state = "active"
            logger.info("source_recovered", source=source.name)

        return cast("dict[str, Any]", data)

    # ── 规则下载 ───────────────────────────────────────

    async def download_rule(self, source_name: str, rule_entry: Dict[str, Any]) -> Path:
        """按需下载单条规则文件 (S39 + SHA256)。"""
        source = self._sources.get(source_name)
        if not source:
            raise ValueError(f"源不存在: {source_name}")

        download_url = rule_entry.get("download_url", "")
        expected_sha256 = rule_entry.get("sha256", "")
        rule_name = rule_entry.get("name", "")

        if not download_url or not rule_name:
            raise ValueError("rule_entry 缺少 download_url 或 name")

        # S39: download_url 必须与 manifest URL 同源
        manifest_netloc = urlparse(source.url).netloc
        dl_netloc = urlparse(download_url).netloc
        if dl_netloc != manifest_netloc:
            raise ValueError(f"download_url 跨源: {download_url} (manifest={manifest_netloc})")

        async with self._global_sem:
            sem = self._per_source_sems.get(source_name)
            if sem:
                async with sem:
                    return await self._do_download_rule(source, download_url, rule_name, expected_sha256)
            return await self._do_download_rule(source, download_url, rule_name, expected_sha256)

    async def _do_download_rule(
        self,
        source: SourceState,
        url: str,
        rule_name: str,
        expected_sha256: str,
    ) -> Path:
        await self._rate_limit()

        # S05: DNS 重绑定防护 — 硬阻断
        await check_dns_rebinding(url)

        safe_url = _log_safe_url(url)

        try:
            async with self._session.get(
                url,
                proxy=self._proxy_url,
                timeout=aiohttp.ClientTimeout(total=SOURCE_DOWNLOAD_TIMEOUT),
                max_redirects=MAX_REDIRECTS,
            ) as resp:
                # H2: 重定向跨源检查
                _check_redirect_not_cross_origin(str(resp.url), url)

                if resp.status != 200:
                    raise ValueError(f"规则下载 HTTP {resp.status}: {safe_url}")

                # H2/M3: Content-Type 检查
                ct = resp.headers.get("Content-Type", "")
                if "application/json" not in ct and "text/json" not in ct:
                    raise ValueError(f"非 JSON Content-Type: {ct}")

                raw = await resp.read()
                if len(raw) > MAX_RULE_FILE_SIZE:
                    raise ValueError(f"规则文件超过 {MAX_RULE_FILE_SIZE} 字节")

                # SHA256 校验
                if expected_sha256:
                    actual = hashlib.sha256(raw).hexdigest()
                    if actual != expected_sha256:
                        raise ValueError(f"SHA256 不匹配: expected={expected_sha256[:16]}... actual={actual[:16]}...")

        except aiohttp.ClientError as e:
            raise ValueError(f"下载失败: {safe_url} ({e})") from e

        # 原子写入缓存目录
        source_dir = self._cache_dir / source.name
        source_dir.mkdir(parents=True, exist_ok=True)
        # N17: 路径遍历防护 — 仅保留文件名最后一段
        safe_rule_name = Path(rule_name).name
        target = source_dir / f"{safe_rule_name}.json"

        data = json.loads(raw.decode("utf-8"))
        safe_write_rule_file(target, data)

        logger.debug("rule_downloaded", source=source.name, rule=safe_rule_name)
        return target

    # ── 批量更新 ───────────────────────────────────────

    async def update_source(self, source_name: str, dry_run: bool = False) -> Dict[str, Any]:
        """更新单个源：下载 manifest → hash diff → 按需下载规则。

        M2: Degraded 源冷却期满后放行探测（对标代理 HALF_OPEN）。
        M5: 哈希 TOCTOU 修复——先持久化 hash 再下载规则。
        返回: {updated: bool, rules_downloaded: int, diff: dict}
        """
        source = self._sources.get(source_name)
        if not source:
            raise ValueError(f"源不存在: {source_name}")

        # M2: Degraded 状态机——冷却期满后放行探测
        if source.state == "degraded":
            if time.monotonic() - source.degraded_at < SOURCE_DEGRADED_COOLDOWN:
                logger.debug("source_update_skip", reason="degraded_cooldown", source=source_name)
                return {"updated": False, "rules_downloaded": 0, "diff": {}}
            logger.info("source_probing", source=source_name)

        # N9: 每日更新次数限制
        today = time.strftime("%Y-%m-%d")
        if source.daily_update_date != today:
            source.daily_update_count = 0
            source.daily_update_date = today
        if source.daily_update_count >= SOURCE_DAILY_UPDATE_LIMIT:
            logger.info("source_update_skip", reason="daily_limit", source=source_name)
            return {"updated": False, "rules_downloaded": 0, "diff": {}}
        source.daily_update_count += 1

        try:
            manifest = await self.fetch_manifest(source_name)
        except Exception as exc:
            logger.warning("manifest_fetch_failed", source=source_name, error=exc)
            source.consecutive_failures += 1
            if source.consecutive_failures >= 3:
                source.state = "degraded"
                source.degraded_at = time.monotonic()
                logger.warning("source_degraded", source=source_name, failures=source.consecutive_failures)
            raise

        # 增量更新：manifest hash 未变则跳过规则下载
        new_hash = source.last_manifest_hash
        stored = get_source_from_file(source_name)
        old_hash = stored.get("last_manifest_hash", "") if stored else ""
        if old_hash == new_hash and old_hash:
            # M12: 哈希匹配 = 积极信号（源可访问），重置失败计数
            source.consecutive_failures = 0
            logger.debug("source_update_skip", reason="manifest_unchanged", source=source_name)
            return {"updated": False, "rules_downloaded": 0, "diff": {}}

        if dry_run:
            return {"updated": False, "rules_downloaded": 0, "diff": {}}

        # M5: TOCTOU 修复——先持久化 hash 再下载规则，重复执行幂等
        self._persist_source_metadata(source_name, new_hash)

        # 下载规则
        rules = manifest.get("rules", [])
        downloaded = 0
        for entry in rules:
            try:
                await self.download_rule(source_name, entry)
                downloaded += 1
            except Exception as exc:
                logger.warning(
                    "rule_download_failed",
                    source=source_name,
                    rule=entry.get("name", ""),
                    error=exc,
                )

        return {"updated": True, "rules_downloaded": downloaded, "diff": {}}

    async def update_all(self, dry_run: bool = False) -> Dict[str, Any]:
        """更新所有源 (N84: 离线静默跳过)。

        返回: {sources_updated: [str], sources_skipped: int}
        调用方可根据 sources_updated 是否非空决定是否触发 reload。
        """
        if not self._auto_update:
            logger.debug("source_update_skip", reason="auto_update_disabled")
            return {"sources_updated": [], "sources_skipped": 0}

        updated: List[str] = []
        skipped = 0
        for name in list(self._sources.keys()):
            source = self._sources[name]
            if source.state in ("emergency_disabled", "moved"):
                skipped += 1
                continue
            try:
                result = await self.update_source(name, dry_run=dry_run)
                if result.get("updated"):
                    updated.append(name)
                else:
                    skipped += 1
            except Exception as exc:
                logger.debug("source_update_error", source=name, error=exc)
                skipped += 1
        return {"sources_updated": updated, "sources_skipped": skipped}

    def _persist_source_metadata(self, source_name: str, manifest_hash: str) -> None:
        """更新 sources.json 中某源的 last_manifest_hash。"""
        found = update_source_in_file(
            source_name,
            last_manifest_hash=manifest_hash,
            last_updated=time.time(),
        )
        if not found:
            source = self._sources.get(source_name)
            if source:
                add_source_to_file(
                    source_name,
                    source.url,
                    title=source.title,
                    maintainer=source.maintainer,
                    homepage=source.homepage,
                    last_manifest_hash=manifest_hash,
                    last_updated=time.time(),
                )

    # ── 辅助 ──────────────────────────────────────────

    async def _rate_limit(self) -> None:
        """S38: 请求间隔 ≥100ms。"""
        now = time.monotonic()
        if self._last_request > 0:
            elapsed = now - self._last_request
            if elapsed < 0.1:
                await asyncio.sleep(0.1 - elapsed)
        self._last_request = time.monotonic()


# ═══════════════════════════════════════════════════════════════════
# URL 安全校验
# ═══════════════════════════════════════════════════════════════════


def _validate_source_url(url: str) -> str:
    """校验远程源 URL (S01/S06/S07)。"""
    if not url or len(url) > MAX_SOURCE_URL_LENGTH:
        raise ValueError(f"URL 长度超限: {len(url)}")

    parsed = urlparse(url)

    # S01+S06: 强制 HTTPS，拒绝 file/ftp 等协议
    if parsed.scheme != "https":
        raise ValueError(f"仅支持 HTTPS: {url}")

    # S07: 拒绝 URL 中含 @ 认证信息
    if "@" in parsed.netloc:
        raise ValueError("URL 不得包含认证信息")
    # 检查 user:pass@host 模式 (https://user:pass@evil.com)
    stripped = url
    for pfx in _AUTH_DETECT_PREFIXES:
        if stripped.startswith(pfx):
            stripped = stripped[len(pfx) :]
            break
    if "@" in stripped.split("/")[0]:
        raise ValueError("URL 不得包含认证信息")

    # 规范化
    return url.rstrip("/")


def _check_redirect_not_cross_origin(resp_url: str, original_url: str) -> None:
    """S03/H2: HTTP 重定向后 URL 必须与原始 URL 同源。"""
    if resp_url == original_url:
        return
    if urlparse(resp_url).netloc != urlparse(original_url).netloc:
        raise ValueError(f"重定向跨源: {original_url} -> {resp_url}")


async def check_dns_rebinding(url: str) -> None:
    """S05: DNS 重绑定防护 — 解析后检查 IP 段，失败时抛出。"""
    hostname = urlparse(url).hostname
    if not hostname:
        raise ValueError("URL 无有效 hostname")

    try:
        loop = asyncio.get_running_loop()
        addrs = await loop.getaddrinfo(hostname, 443)
        for addr_info in addrs:
            ip_str = addr_info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
                for net in _PRIVATE_NETS:
                    if ip in net:
                        raise ValueError(f"DNS 解析到私有/保留 IP: {ip_str} (S05)")
            except ValueError as e:
                if "S05" in str(e):
                    raise
    except ValueError:
        raise
    except Exception:
        logger.warning("dns_check_failed", hostname=hostname)


# 公共别名
validate_source_url = _validate_source_url
