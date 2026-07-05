from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import aiosqlite

from astrocrawl._types import EnqueueResult
from astrocrawl.utils.url import parse_domain, safe_log_url

if TYPE_CHECKING:
    from astrocrawl.storage._protocol import CrawlStateConfig


class _Rollback(Exception):
    """事务正常回滚——非错误路径。_transaction() 捕获后执行 ROLLBACK 并重新抛出。"""


class CrawlState:
    def __init__(self, db_path: str, cfg: CrawlStateConfig):
        self.db_path = db_path
        self.cfg = cfg
        self._conn: Optional[aiosqlite.Connection] = None
        self._log = logging.getLogger("astrocrawl.state")
        self._write_lock = asyncio.Lock()
        self._queue_not_empty = asyncio.Event()

    @property
    def _db(self) -> aiosqlite.Connection:
        """Typed connection accessor — open() must be called first."""
        assert self._conn is not None, "CrawlState.open() must be called before accessing database"
        return self._conn

    @asynccontextmanager
    async def _transaction(self):
        """唯一显式事务入口。_write_lock + BEGIN IMMEDIATE → yield → COMMIT/ROLLBACK。

        _Rollback 被捕获后执行 ROLLBACK 并重新抛出（由调用方捕获决定返回值）。
        Exception/CancelledError 执行 ROLLBACK 后传播（异常出口）。
        """
        async with self._write_lock:
            await self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
                await self._db.commit()
            except _Rollback:
                await self._db.execute("ROLLBACK")
                raise
            except (Exception, asyncio.CancelledError):
                try:
                    await self._db.execute("ROLLBACK")
                except (Exception, asyncio.CancelledError):
                    pass
                raise

    async def open(self) -> None:
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._conn = await aiosqlite.connect(
            self.db_path,
            isolation_level=None,
        )  # isolation_level=None → SQLite 真 auto-commit，避免隐式 DEFERRED 事务
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA cache_size=-20000")
        await self._db.execute("PRAGMA busy_timeout = 500")
        async with self._db.execute("PRAGMA quick_check") as cur:
            row = await cur.fetchone()
            if row and row[0] != "ok":
                raise RuntimeError(f"数据库完整性检查失败: {row[0]}")
        await self._create_tables()
        for col in ("url TEXT", "created_at REAL"):
            try:
                await self._db.execute(f"ALTER TABLE content_hashes ADD COLUMN {col}")
            except Exception:
                pass
        try:
            await self._db.execute("ALTER TABLE failures RENAME COLUMN retry_count TO requeue_count")
        except Exception:
            pass
        try:
            await self._db.execute("ALTER TABLE urls ADD COLUMN outcome TEXT DEFAULT ''")
        except Exception:
            pass
        # URL Frontier: queue 表新增 domain 列
        try:
            await self._db.execute("ALTER TABLE queue ADD COLUMN domain TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass
        # 回填旧数据的 domain 列
        async with self._db.execute("SELECT COUNT(*) FROM queue WHERE domain = ''") as cur:
            row = await cur.fetchone()
            empty_count = row[0] if row else 0
        if empty_count > 0:
            self._log.info("event=db_backfill_domain count=%d", empty_count)
            async with self._db.execute("SELECT id, url FROM queue WHERE domain = ''") as cur:
                rows = list(await cur.fetchall())
            for batch_start in range(0, len(rows), 500):
                batch = rows[batch_start : batch_start + 500]
                for row_id, url in batch:
                    domain = parse_domain(url)
                    await self._db.execute("UPDATE queue SET domain = ? WHERE id = ?", (domain, row_id))
                await self._db.commit()
        await self._db.commit()
        await self._maybe_migrate_from_old_tables()
        await self._recover_in_flight()
        async with self._db.execute("SELECT 1 FROM queue LIMIT 1") as cur:
            if await cur.fetchone() is not None:
                self._queue_not_empty.set()

    async def urls_table_empty(self) -> bool:
        """检查 urls 表是否为空（无任何已访问记录）。"""
        async with self._db.execute("SELECT 1 FROM urls LIMIT 1") as cur:
            return not bool(await cur.fetchone())

    async def _create_tables(self) -> None:
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS visited_urls (
                url TEXT PRIMARY KEY,
                depth INTEGER,
                added_time REAL
            );
            CREATE TABLE IF NOT EXISTS completed_urls (
                url TEXT PRIMARY KEY,
                completed_time REAL
            );
            CREATE TABLE IF NOT EXISTS urls (
                url TEXT PRIMARY KEY,
                depth INTEGER DEFAULT 0,
                status TEXT DEFAULT 'visited',
                added_time REAL,
                completed_time REAL
            );
            CREATE TABLE IF NOT EXISTS content_hashes (
                hash TEXT PRIMARY KEY,
                url TEXT,
                created_at REAL
            );
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE,
                depth INTEGER NOT NULL,
                added_time REAL,
                domain TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS failures (
                url TEXT PRIMARY KEY,
                depth INTEGER,
                error TEXT,
                timestamp REAL,
                requeue_count INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS in_flight (
                url TEXT PRIMARY KEY,
                depth INTEGER,
                started_at REAL
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS boundary_links (
                parent_url TEXT,
                child_url TEXT,
                parent_depth INTEGER NOT NULL,
                PRIMARY KEY (parent_url, child_url)
            );
        """)
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_queue_depth_id ON queue(depth, id)")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_queue_domain_depth_id ON queue(domain, depth, id)")
        await self._db.commit()

    async def _maybe_migrate_from_old_tables(self) -> None:
        async with self._db.execute("SELECT COUNT(*) FROM urls") as cur:
            if (await cur.fetchone())[0] > 0:  # type: ignore[index]
                return
        async with self._db.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN ('visited_urls', 'completed_urls')"
        ) as cur:
            if (await cur.fetchone())[0] < 2:  # type: ignore[index]
                return
        async with self._db.execute("SELECT COUNT(*) FROM visited_urls") as cur:
            visited_count = (await cur.fetchone())[0]  # type: ignore[index]
        async with self._db.execute("SELECT COUNT(*) FROM completed_urls") as cur:
            completed_count = (await cur.fetchone())[0]  # type: ignore[index]
        if visited_count == 0 and completed_count == 0:
            return
        self._log.info("event=db_migrate_legacy visited=%d completed=%d", visited_count, completed_count)
        await self._db.executescript("""
            INSERT OR IGNORE INTO urls(url, depth, status, added_time)
            SELECT url, depth, 'visited', added_time FROM visited_urls;
            INSERT OR IGNORE INTO urls(url, depth, status, completed_time)
            SELECT url, 0, 'completed', completed_time FROM completed_urls;
        """)
        await self._db.execute("UPDATE urls SET status = 'completed' WHERE url IN (SELECT url FROM completed_urls)")
        await self._db.commit()

    async def _recover_in_flight(self) -> None:
        """启动时从 in_flight 表恢复未完成的 URL 到队列。"""
        async with self._db.execute("SELECT url, depth FROM in_flight") as cur:
            in_flight = await cur.fetchall()
        if not in_flight:
            return
        recovered = 0
        skipped = 0
        async with self._transaction():
            for url, depth in in_flight:
                async with self._db.execute("SELECT 1 FROM urls WHERE url=? AND status='completed'", (url,)) as cur:
                    if await cur.fetchone() is not None:
                        skipped += 1
                        continue
                domain = parse_domain(url)
                async with self._db.execute(
                    "INSERT OR IGNORE INTO queue(url, depth, added_time, domain) VALUES(?,?,?,?)",
                    (url, depth, time.time(), domain),
                ) as cur:
                    if cur.rowcount > 0:
                        recovered += 1
            await self._db.execute("DELETE FROM in_flight")
        if recovered > 0:
            self._log.warning("event=crash_recovery_requeue count=%d", recovered)
            self._queue_not_empty.set()
        if skipped > 0:
            self._log.info("event=crash_recovery_skip count=%d", skipped)

    async def flush(self) -> None:
        """Best-effort 提交——确保 WAL 中所有已提交事务对后续连接可见。"""
        if self._conn:
            try:
                await self._db.commit()
            except Exception:
                pass

    async def close(self) -> None:
        if self._conn:
            try:
                await self._db.close()
            except Exception as e:
                self._log.warning("event=db_close_error error=%s", e)

    async def mark_completed(self, url: str, depth: int = 0, original_url: str = "", outcome: str = "") -> bool:
        now = time.time()
        async with self._transaction():
            await self._db.execute(
                "INSERT INTO urls(url, depth, status, added_time, completed_time, outcome) "
                "VALUES(?, ?, 'completed', ?, ?, ?) "
                "ON CONFLICT(url) DO UPDATE SET status='completed', "
                "depth=excluded.depth, completed_time=excluded.completed_time, "
                "outcome=excluded.outcome",
                (url, depth, now, now, outcome),
            )
            await self._db.execute("DELETE FROM failures WHERE url=? OR url=?", (original_url or url, url))
            await self._db.execute("DELETE FROM in_flight WHERE url=? OR url=?", (original_url or url, url))
        return True

    async def clean_content_hashes(self, cutoff: float) -> None:
        async with self._write_lock:
            async with self._db.execute("DELETE FROM content_hashes WHERE created_at < ?", (cutoff,)):
                pass

    async def add_content_hash(self, h: str, url: str = "") -> bool:
        async with self._write_lock:
            async with self._db.execute(
                "INSERT OR IGNORE INTO content_hashes(hash, url, created_at) VALUES(?,?,?)", (h, url, time.time())
            ) as cur:
                rowcount: int = cur.rowcount
        return rowcount > 0

    # ── 队列操作 ──────────────────────────────────────────────

    async def _check_queue_full(self) -> bool:
        async with self._db.execute("SELECT COUNT(*) FROM queue") as cur:
            row = await cur.fetchone()
            return (row[0] if row else 0) >= self.cfg.queue_hard_maxsize

    async def is_queue_full(self) -> bool:
        return await self._check_queue_full()

    async def push_to_queue_single(self, url: str, depth: int, domain: str = "") -> EnqueueResult:
        """入队一个 URL。自动去重（urls/queue/in_flight）。

        Returns EnqueueResult 精确区分入队/队列满/重复，消除 bool 信息丢失。
        """
        if not domain:
            domain = parse_domain(url)
        result = EnqueueResult.ENQUEUED
        try:
            async with self._transaction():
                async with self._db.execute("SELECT COUNT(*) FROM queue") as cur:
                    row = await cur.fetchone()
                    if (row[0] if row else 0) >= self.cfg.queue_hard_maxsize:
                        result = EnqueueResult.QUEUE_FULL
                        raise _Rollback()
                async with self._db.execute(
                    "SELECT 1 FROM urls WHERE url=?"
                    " UNION ALL SELECT 1 FROM queue WHERE url=?"
                    " UNION ALL SELECT 1 FROM in_flight WHERE url=?"
                    " LIMIT 1",
                    (url, url, url),
                ) as cur:
                    if await cur.fetchone() is not None:
                        result = EnqueueResult.DUPLICATE
                        raise _Rollback()
                async with self._db.execute(
                    "INSERT OR IGNORE INTO queue(url, depth, added_time, domain) VALUES(?,?,?,?)",
                    (url, depth, time.time(), domain),
                ) as cur:
                    inserted = cur.rowcount > 0
                if not inserted:
                    result = EnqueueResult.DUPLICATE
                    raise _Rollback()
                await self._db.execute("DELETE FROM in_flight WHERE url=?", (url,))
            self._queue_not_empty.set()
            return EnqueueResult.ENQUEUED
        except _Rollback:
            return result

    async def push_to_queue_as_owner(self, url: str, depth: int, domain: str = "") -> bool:
        """免费重入队（绕过 in_flight 检查，不计入 max_requeue）。
        用于基础设施故障/停止中断。"""
        if not domain:
            domain = parse_domain(url)
        try:
            async with self._transaction():
                async with self._db.execute("SELECT COUNT(*) FROM queue") as cur:
                    row = await cur.fetchone()
                    if (row[0] if row else 0) >= self.cfg.queue_hard_maxsize:
                        raise _Rollback()
                async with self._db.execute(
                    "SELECT 1 FROM urls WHERE url=? UNION ALL SELECT 1 FROM queue WHERE url=? LIMIT 1", (url, url)
                ) as cur:
                    if await cur.fetchone() is not None:
                        raise _Rollback()
                async with self._db.execute(
                    "INSERT OR IGNORE INTO queue(url, depth, added_time, domain) VALUES(?,?,?,?)",
                    (url, depth, time.time(), domain),
                ) as cur:
                    inserted = cur.rowcount > 0
                if not inserted:
                    raise _Rollback()
                await self._db.execute("DELETE FROM in_flight WHERE url=?", (url,))
            self._queue_not_empty.set()
            return True
        except _Rollback:
            return False

    async def pop_from_domain(self, domain: str) -> Optional[Tuple[str, int]]:
        """域名感知出队。从指定域的子队列弹出 URL 并登记 in_flight。"""
        try:
            async with self._transaction():
                async with self._db.execute(
                    "SELECT id, url, depth FROM queue WHERE domain = ? ORDER BY depth, id LIMIT 1", (domain,)
                ) as cur:
                    row = await cur.fetchone()
                    if row is None:
                        raise _Rollback()
                async with self._db.execute("DELETE FROM queue WHERE id=?", (row[0],)):
                    pass
                await self._db.execute(
                    "INSERT OR IGNORE INTO in_flight(url, depth, started_at) VALUES(?,?,?)",
                    (row[1], row[2], time.time()),
                )
            return row[1], row[2]
        except _Rollback:
            return None

    async def get_active_domains(self) -> List[str]:
        """返回有排队 URL 的域名列表——直接从 queue 表查，利用 idx_queue_domain_depth_id 索引。"""
        async with self._db.execute("SELECT DISTINCT domain FROM queue WHERE domain != '' ORDER BY domain") as cur:
            rows = await cur.fetchall()
        return [row[0] for row in rows]

    async def queue_size(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM queue") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def wait_for_queue(self, timeout: float = 0.5) -> None:
        try:
            await asyncio.wait_for(self._queue_not_empty.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    # ── in_flight 操作 ──────────────────────────────────────

    async def in_flight_count(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM in_flight") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def url_in_flight(self, url: str) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM in_flight WHERE url = ? LIMIT 1",
            (url,),
        ) as cur:
            row = await cur.fetchone()
            return row is not None

    async def force_fail_all_in_flight(self, max_requeue: int) -> int:
        """关闭路径：将所有 in_flight URL 标记为永久失败。"""
        async with self._db.execute("SELECT COUNT(*) FROM in_flight") as cur:
            row = await cur.fetchone()
            total = row[0] if row else 0
        if total == 0:
            return 0
        now = time.time()
        async with self._transaction():
            async with self._db.execute("SELECT url, depth FROM in_flight") as cur:
                rows = await cur.fetchall()
            for url, depth in rows:
                await self._db.execute(
                    "INSERT OR REPLACE INTO failures(url, depth, error, timestamp, requeue_count) VALUES(?,?,?,?,?)",
                    (url, depth, "shutdown force-fail", now, max_requeue),
                )
            await self._db.execute("DELETE FROM in_flight")
        if total:
            self._log.warning("event=in_flight_purge count=%d", total)
        return total

    # ── 完成查询 ────────────────────────────────────────────

    async def completed_count(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM urls WHERE status='completed'") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    # ── 重试 / 失败 ─────────────────────────────────────────

    async def try_schedule_retry(self, url: str, depth: int) -> bool:
        """计费重入队：管理 queue / in_flight / failures 原子操作。"""
        try:
            async with self._transaction():
                async with self._db.execute("SELECT COUNT(*) FROM queue") as cur:
                    row = await cur.fetchone()
                    if (row[0] if row else 0) >= self.cfg.queue_hard_maxsize:
                        self._log.warning("event=retry_queue_full url=%s", safe_log_url(url))
                        raise _Rollback()
                async with self._db.execute("SELECT 1 FROM urls WHERE url=? AND status='completed'", (url,)) as cur:
                    if await cur.fetchone() is not None:
                        raise _Rollback()
                await self._db.execute(
                    "INSERT OR IGNORE INTO failures(url, depth, error, timestamp, requeue_count) VALUES(?,?,?,?,0)",
                    (url, depth, "", time.time()),
                )
                async with self._db.execute("SELECT requeue_count FROM failures WHERE url=?", (url,)) as cur:
                    row = await cur.fetchone()
                    current = row[0] if row else 0
                if current >= self.cfg.max_requeue:
                    self._log.info("event=retry_exhausted url=%s", safe_log_url(url))
                    raise _Rollback()
                domain = parse_domain(url)
                await self._db.execute(
                    "INSERT OR IGNORE INTO queue(url, depth, added_time, domain) VALUES(?,?,?,?)",
                    (url, depth, time.time(), domain),
                )
                await self._db.execute("UPDATE failures SET requeue_count = requeue_count + 1 WHERE url=?", (url,))
                await self._db.execute("DELETE FROM in_flight WHERE url=?", (url,))
            self._queue_not_empty.set()
            return True
        except _Rollback:
            return False

    async def log_failure(self, url: str, depth: int, error: str, permanent: bool = False) -> None:
        async with self._transaction():
            await self._db.execute(
                "INSERT OR IGNORE INTO failures(url, depth, error, timestamp, requeue_count) VALUES(?,?,?,?,0)",
                (url, depth, error, time.time()),
            )
            if permanent:
                await self._db.execute("DELETE FROM in_flight WHERE url=?", (url,))
                await self._db.execute(
                    "UPDATE failures SET depth=?, requeue_count = ?, error = ? WHERE url=?",
                    (depth, self.cfg.max_requeue, error, url),
                )
            else:
                await self._db.execute(
                    "UPDATE failures SET depth=?, error=?, timestamp=? WHERE url=?", (depth, error, time.time(), url)
                )

    async def atomic_retry_reclaim(self, max_requeue: int) -> Optional[Tuple[str, int]]:
        """原子化查找可重试失败 URL + 入队 + 增加 requeue_count。
        _retry_monitor 后台任务调用。"""
        try:
            async with self._transaction():
                async with self._db.execute("SELECT COUNT(*) FROM queue") as cur:
                    row = await cur.fetchone()
                    if (row[0] if row else 0) >= self.cfg.queue_hard_maxsize:
                        raise _Rollback()
                async with self._db.execute(
                    """SELECT f.url, f.depth FROM failures f
                       WHERE f.requeue_count < ?
                         AND NOT EXISTS (SELECT 1 FROM queue q WHERE q.url = f.url)
                         AND NOT EXISTS (SELECT 1 FROM in_flight i WHERE i.url = f.url)
                         AND NOT EXISTS (SELECT 1 FROM urls u WHERE u.url = f.url AND u.status = 'completed')
                       ORDER BY f.depth
                       LIMIT 1""",
                    (max_requeue,),
                ) as cur:
                    row = await cur.fetchone()
                    if row is None:
                        raise _Rollback()
                    url, depth = row
                domain = parse_domain(url)
                async with self._db.execute(
                    "INSERT OR IGNORE INTO queue(url, depth, added_time, domain) VALUES(?,?,?,?)",
                    (url, depth, time.time(), domain),
                ) as cur:
                    if cur.rowcount == 0:
                        raise _Rollback()
                await self._db.execute("UPDATE failures SET requeue_count = requeue_count + 1 WHERE url=?", (url,))
            self._queue_not_empty.set()
            return (url, depth)
        except _Rollback:
            return None

    async def peek_retryable(self, max_requeue: int) -> Optional[Tuple[str, int, int]]:
        """纯读：查找一个可重试的失败 URL。返回 (url, depth, requeue_count) 或 None。"""
        async with self._db.execute(
            """SELECT f.url, f.depth, f.requeue_count FROM failures f
               WHERE f.requeue_count < ?
                 AND NOT EXISTS (SELECT 1 FROM urls u WHERE u.url = f.url AND u.status = 'completed')
               ORDER BY f.depth
               LIMIT 1""",
            (max_requeue,),
        ) as cur:
            row = await cur.fetchone()
            return (row[0], row[1], row[2]) if row else None

    async def failure_count(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM failures") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def retryable_failure_count(self, max_requeue: int) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM failures WHERE requeue_count < ?", (max_requeue,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def permanent_failure_count(self, max_requeue: int) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM failures WHERE requeue_count >= ?", (max_requeue,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def counts_by_outcome(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        async with self._db.execute(
            "SELECT outcome, COUNT(*) FROM urls WHERE status='completed' AND outcome != '' GROUP BY outcome"
        ) as cur:
            for row in await cur.fetchall():
                result[row[0]] = row[1]
        return result

    # ── 管理操作 ────────────────────────────────────────────

    async def reset_all(self) -> None:
        await self._db.executescript("""
            DELETE FROM urls;
            DELETE FROM content_hashes;
            DELETE FROM queue;
            DELETE FROM failures;
            DELETE FROM in_flight;
            DELETE FROM boundary_links;
            DELETE FROM meta;
        """)
        await self._db.commit()
        self._queue_not_empty.clear()

    async def reset_queue_only(self) -> None:
        """仅清空队列（不重置完成/失败记录）。"""
        async with self._transaction():
            await self._db.execute("DELETE FROM queue")
        async with self._db.execute("SELECT 1 FROM queue LIMIT 1") as cur:
            if await cur.fetchone() is None:
                self._queue_not_empty.clear()

    async def purge_orphaned_queue_entries(self) -> int:
        """删除队列中 domain 为空的孤立条目。返回删除数量。

        当 queue_size() > 0 但 get_active_domains() 返回空列表时调用。
        对标 Mercator：found URLs in frontier but no domains match — clean orphans.
        """
        async with self._write_lock:
            async with self._db.execute("SELECT COUNT(*) FROM queue WHERE domain = ''") as cur:
                row = await cur.fetchone()
                count = row[0] if row else 0
            if count == 0:
                return 0
            async with self._db.execute("DELETE FROM queue WHERE domain = ''"):
                pass
        if count:
            self._log.warning("event=orphan_purge count=%d", count)
        async with self._db.execute("SELECT 1 FROM queue LIMIT 1") as cur:
            if await cur.fetchone() is None:
                self._queue_not_empty.clear()
        return count

    async def set_meta(self, key: str, value: str) -> None:
        async with self._write_lock:
            async with self._db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, value)):
                pass

    async def get_meta(self, key: str, default: str = "") -> str:
        async with self._db.execute("SELECT value FROM meta WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else default

    async def save_boundary_links(
        self,
        parent_url: str,
        child_urls: list,
        parent_depth: int,
    ) -> int:
        if not child_urls:
            return 0
        # 纯追加 INSERT OR IGNORE，不持 _write_lock——无需原子性，SQLite WAL + UNIQUE 约束足够
        saved = 0
        for child in child_urls:
            async with self._db.execute(
                "INSERT OR IGNORE INTO boundary_links(parent_url, child_url, parent_depth) VALUES(?,?,?)",
                (parent_url, child, parent_depth),
            ) as cur:
                if cur.rowcount > 0:
                    saved += 1
        if saved > 0:
            self._log.debug("event=boundary_links_saved count=%d parent_depth=%d", saved, parent_depth)
        return saved

    async def promote_boundary_links(
        self,
        new_depth: int,
    ) -> list:
        try:
            async with self._transaction():
                async with self._db.execute(
                    """SELECT child_url, MIN(parent_depth + 1)
                       FROM boundary_links
                       WHERE parent_depth + 1 < ?
                       GROUP BY child_url""",
                    (new_depth,),
                ) as cur:
                    rows = await cur.fetchall()
                if not rows:
                    raise _Rollback()
                async with self._db.execute("DELETE FROM boundary_links WHERE parent_depth + 1 < ?", (new_depth,)):
                    pass
            result = [(row[0], row[1]) for row in rows]
            self._log.info("event=boundary_links_promoted count=%d new_depth=%d", len(result), new_depth)
            return result
        except _Rollback:
            return []

    async def boundary_links_count(self) -> int:
        async with self._db.execute("SELECT COUNT(*) FROM boundary_links") as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

    async def get_lost_children(self, parent_depth: int) -> List[str]:
        result: List[str] = []
        async with self._db.execute(
            """SELECT DISTINCT bl.child_url
               FROM boundary_links bl
               WHERE bl.parent_depth = ?
                 AND NOT EXISTS (SELECT 1 FROM urls u WHERE u.url = bl.child_url)
                 AND NOT EXISTS (SELECT 1 FROM queue q WHERE q.url = bl.child_url)
                 AND NOT EXISTS (SELECT 1 FROM in_flight i WHERE i.url = bl.child_url)
            """,
            (parent_depth,),
        ) as cur:
            async for row in cur:
                result.append(row[0])
        return result

    async def completed_count_by_depth(self) -> dict:
        result: dict = {}
        async with self._db.execute("SELECT depth, COUNT(*) FROM urls WHERE status='completed' GROUP BY depth") as cur:
            async for row in cur:
                result[row[0]] = row[1]
        return result

    async def queue_count_by_depth(self) -> dict:
        """按深度统计队列中 URL 数量。"""
        result: dict = {}
        async with self._db.execute("SELECT depth, COUNT(*) FROM queue GROUP BY depth") as cur:
            async for row in cur:
                result[row[0]] = row[1]
        return result

    async def failed_count_by_depth(self) -> dict:
        result: dict = {}
        async with self._db.execute(
            "SELECT depth, COUNT(*) FROM failures WHERE requeue_count >= ? GROUP BY depth", (self.cfg.max_requeue,)
        ) as cur:
            async for row in cur:
                result[row[0]] = row[1]
        return result

    async def purge_failures_depth_ge(self, max_depth: int) -> int:
        async with self._transaction():
            async with self._db.execute("DELETE FROM failures WHERE depth >= ?", (max_depth,)) as cur:
                removed: int = cur.rowcount
        if removed > 0:
            self._log.info("event=depth_purge_failures removed=%d new_max_depth=%d", removed, max_depth)
        return removed

    async def purge_queue_depth_ge(self, max_depth: int) -> int:
        """移除 depth >= max_depth 的队列项并暂存至 boundary_links。"""
        try:
            async with self._transaction():
                async with self._db.execute("SELECT url, depth FROM queue WHERE depth >= ?", (max_depth,)) as cur:
                    rows = list(await cur.fetchall())
                if not rows:
                    raise _Rollback()
                for url, depth in rows:
                    if depth > 0:
                        await self._db.execute(
                            "INSERT OR IGNORE INTO boundary_links(parent_url, child_url, parent_depth) "
                            "VALUES('', ?, ?)",
                            (url, depth - 1),
                        )
                await self._db.execute("DELETE FROM queue WHERE depth >= ?", (max_depth,))
            removed = len(rows)
            self._log.info("event=depth_purge_queue removed=%d new_max_depth=%d", removed, max_depth)
            return removed
        except _Rollback:
            return 0
