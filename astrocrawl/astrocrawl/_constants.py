from __future__ import annotations

# ── 解析规则引擎 ──────────────────────────────────────────────────────────────
# 使用者: rules/_schema.py / rules/_loader.py / rules/_extractor.py
MAX_RULES_TOTAL = 2000  # N11: 规则总数上限
MAX_FALLBACK_DEPTH = 3  # S22: fallback 链最大深度（主 + 2 层）
MAX_FIELDS_PER_RULE = 50  # 单规则最大字段数
MAX_FIELD_NAME_LENGTH = 64  # 字段名最大长度
MULTIPLE_MAX_ITEMS = 1000  # S26: multiple:true 最大返回项数
SELECTOR_TIMEOUT_PER_RULE = 5  # N18: 单规则选择器执行超时 (秒)
MAX_RULE_FILE_SIZE = 64 * 1024  # S14: 单规则文件最大 64KB
MAX_RULES_CACHE_SIZE = 50 * 1024 * 1024  # S16: 规则缓存总量 ≤ 50MB
MAX_JSON_DEPTH = 20  # S24: JSON 最大嵌套深度
RULES_TMP_MAX_AGE_HOURS = 24  # S15: 过期 .tmp 文件清理阈值 (小时)
SOURCE_PRIORITY = {"pip": 0, "remote": 1, "user": 2}  # 规则源优先级，使用者: rules/_loader.py rules/_matcher.py

# ── 远程规则源 ──────────────────────────────────────────────────────────────
# 使用者: rules/_source.py SourceManager
MANIFEST_MAX_BYTES = 1 * 1024 * 1024  # S10: Manifest 响应体最大 1MB
SOURCE_DOWNLOAD_TIMEOUT = 30  # S08: 下载超时 (秒)
SOURCE_DAILY_UPDATE_LIMIT = 12  # N9: 单源每日更新次数上限
DOWNLOAD_CONCURRENCY_GLOBAL = 5  # S38: 全局下载并发上限
DOWNLOAD_CONCURRENCY_PER_SOURCE = 2  # S38: 单源下载并发上限
MAX_REDIRECTS = 5  # S03: HTTP 重定向最大次数
MAX_SOURCE_URL_LENGTH = 2048  # URL 长度上限
SOURCE_DEGRADED_COOLDOWN = 600  # M2: Degraded 源冷却时间 (秒)，对标代理 30s

# ── AI 生成 ──────────────────────────────────────────────────────────────────

RULE_NAME_MAX_LENGTH = 64  # S11: 规则 name 最大长度
RULE_NAME_PATTERN = r"^[a-z0-9_-]+$"  # S11: 规则 name 合法字符
ATTR_NAME_PATTERN = r"^[a-zA-Z0-9_-]+$"  # S29: attr 名合法字符
CURRENCY_SYMBOLS = frozenset({"¥", "$", "€", "£", "₹", "₩", "₽", "₺", "R$"})
TRANSFORM_MEMORY_MULTIPLIER = 5  # N104: replace 内存放大倍数上限

# ── Worker 与队列 ────────────────────────────────────────────────────────────
# 使用者: crawler/engine.py _worker() / _pop_domain_aware() / run() 主循环
WORKER_IDLE_SLEEP = 0.5  # queue 空时 worker 休眠间隔（避免忙等待）

RUN_EMPTY_QUEUE_WAIT = 1  # 队列首次空等待秒数（队列瞬时排空不立即退出）
RUN_EMPTY_QUEUE_CONFIRM = 5  # 队列二次确认等待秒数（排除网络延迟导致的重入队）

# ── 关闭与生命周期 ───────────────────────────────────────────────────────────
# 使用者: crawler/engine.py _run_worker_loop() finally / gui/thread.py CrawlerThread.run()
SHUTDOWN_PENDING_TIMEOUT = 15  # event loop 清理 pending tasks 超时
SHUTDOWN_EXECUTOR_TIMEOUT = 10  # default executor shutdown 超时
SHUTDOWN_ASYNCGEN_TIMEOUT = 10  # async generator shutdown 超时
IN_FLIGHT_DRAIN_TIMEOUT = 30  # 停止后等待 in_flight 自然排空超时

# ── Sitemap 与 robots.txt ────────────────────────────────────────────────────
# 使用者: network/sitemap.py SitemapDiscovery / network/robots.py RobotsCache
# 注: 重试次数与退避基数已统一使用 CrawlerConfig.max_retries / retry_backoff_base
SITEMAP_FETCH_TIMEOUT = 8  # sitemap 单次抓取超时 (秒)
SITEMAP_MAX_CONTENT_SIZE = 50 * 1024 * 1024  # 50 MiB per sitemap fetch
SITEMAP_MAX_DECOMPRESSED = 100 * 1024 * 1024  # 100 MiB decompressed
ROBOTS_FETCH_TIMEOUT = 5  # robots.txt 抓取超时 (秒)
ROBOTS_FETCH_MAX_CONCURRENT = 8  # robots.txt 并发抓取上限
ROBOTS_MAX_SIZE = 500 * 1024  # RFC 9309: 500 KiB 上限

# ── 浏览器上下文与页面管理 ───────────────────────────────────────────────────
# 使用者: browser/context_pool.py ContextPool / browser/page_pool.py PagePool
PAGE_CREATE_RETRIES = 3  # 页面创建最大重试
PAGE_CREATE_BACKOFF = 1.0  # 页面创建重试退避基数 (秒)
PAGE_CREATE_TIMEOUT = 15  # context.new_page() asyncio 超时 (秒)
PAGE_CLOSE_TIMEOUT = 5  # page.close() / context.close() asyncio 超时 (秒)
ABOUT_BLANK_ASYNCIO_TIMEOUT = 5.0  # remove_broken 中 about:blank goto 超时 (秒)
SLOT_CREATE_RETRIES = 3  # 槽位创建最大重试
SLOT_CREATE_BACKOFF = 1.0  # 槽位创建重试退避基数 (秒)
CONTEXT_CREATE_TIMEOUT = 30  # browser.new_context() asyncio 超时 (秒)
RELEASE_UNROUTE_TIMEOUT = 3  # 错误恢复时 page.unroute_all() 超时 (秒)
BROWSER_LAUNCH_TIMEOUT = 60  # Chromium 浏览器启动超时 (秒)
HARD_CLEANUP_TIMEOUT = 15  # finally 块中资源释放的最硬超时 (秒)，不可被 asyncio.shield 绕过

# ── 纵深防御超时 ─────────────────────────────────────────────────────────────
# 使用者: crawler/engine.py _fetch_url() / browser/browser_pool.py / pipeline processors
# Playwright 原生超时 (page.goto timeout) 通过 cfg.page_timeout 配置。
# 以下 asyncio.wait_for 超时作为第二层兜底——当 Playwright 因 TCP SYN 挂起、
# CDP 通道中断等原因不触发自身超时时，asyncio 层强制终止。
CONTENT_READ_TIMEOUT = 15  # page.content() asyncio 超时 (秒)
PROCESS_URL_TIMEOUT = 240  # pipeline 单 URL 处理整体超时 (含 prefer_direct 代理回退余量)
FETCH_PROCESSOR_OVERHEAD = 15  # 非 fetch processor 的时间预算 + 安全余量 (秒)
DOMAIN_CONCURRENCY_ACQUIRE_TIMEOUT = 60  # 域并发 semaphore 获取超时 (秒)

# ── 卡死检测与上下文健康 ────────────────────────────────────────────────────
# 使用者: crawler/engine.py 主循环 / browser/browser_pool.py _health_check_loop()
WORKER_STUCK_TIMEOUT = 300  # 所有 worker 进度停滞阈值 (秒)，超时强制退出
STUCK_IN_FLIGHT_TIMEOUT = 300  # queue=0 但 in_flight>0 的悬挂判定阈值 (秒)
CONTEXT_HEALTH_CHECK_INTERVAL = 30  # 空闲 slot 健康检查间隔 (秒)
BROWSER_GOTO_BUFFER_S = 5.0  # asyncio.wait_for 超时超过 Playwright 超时的缓冲（秒）
BROWSER_RESTART_TIMEOUT_MULT = 3  # Browser 重启超时为 HARD_CLEANUP_TIMEOUT 的倍数

# ── 代理健康管理 ─────────────────────────────────────────────────────────────
# 使用者: proxy/_proxy.py ProxyHealthTracker / ProxyManager
# 断路器模式 (Circuit Breaker): CLOSED → OPEN → HALF_OPEN → CLOSED
PROXY_FAILURE_THRESHOLD = 3  # 连续失败 → OPEN (完全熔断) 的阈值
PROXY_COOLDOWN = 30.0  # OPEN → HALF_OPEN 最短冷却时间 (秒)
PROXY_COOLDOWN_MAX = 120.0  # 冷却时间指数增长上界 (秒)
PROXY_HALF_OPEN_MIN_DURATION = 15.0  # HALF_OPEN 最短考察窗口 (秒)
PROXY_HALF_OPEN_MAX_FAILURES = 2  # HALF_OPEN 窗口内最大容忍失败数
PROXY_DECAY_SECONDS = 120.0  # 故障记录过期时间 (秒)，超时后该次失败不再计入 health_score
PROXY_PROBE_INTERVAL = 5.0  # 后台 TCP 探测间隔 (秒)
PROXY_PROBE_TIMEOUT = 2.0  # 单次 TCP 连接超时 (秒)
PROXY_SCORE_WINDOW = 15.0  # health_score 故障密度窗口 (秒)
PROXY_SCORE_SUCCESS_DECAY = 30.0  # 成功奖励线性衰减 (秒)
PROXY_HEALTH_BAR_REFRESH = 3.0  # GUI 健康条刷新间隔 (秒)

# ── 域管理 ───────────────────────────────────────────────────────────────────
# 使用者: network/throttling.py / crawler/engine.py run()

DOMAIN_CLEANUP_AGE = 3600  # 域限流条目闲置后清理阈值 (秒)
DNS_CACHE_TTL = 300  # aiohttp DNS 缓存 TTL (秒)

# ── 后台维护周期 ─────────────────────────────────────────────────────────────
# 使用者: crawler/engine.py run() — HealthMonitor 调度
RETRY_MONITOR_INTERVAL = 10  # 失败重试监视器轮询间隔 (秒)
RETRY_MONITOR_BATCH = 50  # 每批原子重试回收最大数量
RATE_LIMITER_CLEANUP_INTERVAL = 600  # 域速率限制器过期条目清理间隔 (秒)
CONCURRENT_LIMITER_CLEANUP_INTERVAL = 300  # 域并发限制器过期条目清理间隔 (秒)
RESOURCE_MONITOR_INTERVAL = 60  # 资源监控 (内存/DB/队列) 间隔 (秒)
HASH_CLEANUP_INTERVAL = 1800  # 内容哈希表过期条目清理间隔 (秒)
HASH_MAX_AGE = 86400.0  # 内容哈希条目最大保留时间 (秒)，24h

# ── 连接器 ───────────────────────────────────────────────────────────────────
# 使用者: crawler/engine.py run() — aiohttp TCPConnector
CONNECTOR_LIMIT = 100  # aiohttp 总连接数上限
CONNECTOR_LIMIT_PER_HOST = 10  # aiohttp 单主机连接数上限

# ── 诊断 ───────────────────────────────────────────────────────────────────────
# 使用者: diagnostics.py CrawlDiagnostics
HTTP_READ_LINE_TIMEOUT = 10.0  # HTTP 健康端点 readline() 超时 (秒)

# ── 日志 ─────────────────────────────────────────────────────────────────────
# 使用者: astrobase._logging / gui/main_window.py / browser/browser_pool.py (MAX_ERROR_MESSAGE_LENGTH)
FILE_LOG_MAX_BYTES = 10 * 1024 * 1024  # RotatingFileHandler 单文件最大字节数
FILE_LOG_BACKUP_COUNT = 3  # RotatingFileHandler 历史备份文件数
MAX_LOG_ITEMS = 500  # GUI 日志列表最大条目数 (超出时清理)
MAX_ERROR_MESSAGE_LENGTH = 500  # 错误消息截断长度 (防止日志溢出)

# ── GUI 控件物理极限 ──────────────────────────────────────────────────────────
QSPINBOX_MAX = 2147483647  # QSpinBox int32 上界，非业务上限
QDOUBLESPINBOX_MAX = 1.7976931348623157e308  # QDoubleSpinBox double 上界，非业务上限
QLINEEDIT_MAX = 32767  # QLineEdit Qt 内部默认 maxLength，非业务上限

# ── 下载候选扩展名 (对齐 ADR-0002 下载预设 + Scrapy LinkExtractor) ──────────────
# 使用者: utils/html.py _is_download_candidate() → extract_links_from_soup()
# 链接提取时按扩展名识别非 HTML 资源，当前阶段记录为 DOWNLOAD_CANDIDATE drop，
# 下载模块 (ADR-0002) 落地后路由到下载管线。
DOWNLOAD_EXTENSIONS = frozenset(
    {
        # archives
        "tar",
        "gz",
        "tgz",
        "bz2",
        "xz",
        "z",
        "7z",
        "zip",
        "rar",
        "jar",
        "iso",
        "dmg",
        "exe",
        "pkg",
        "deb",
        "rpm",
        "apk",
        "msi",
        "msix",
        "sigstore",
        # data
        "csv",
        "tsv",
        "json",
        "xml",
        "yaml",
        "yml",
        "sql",
        "sqlite",
        "db",
        "dat",
        "bin",
        "log",
        # ebooks
        "epub",
        "mobi",
        "azw",
        "azw3",
        # documents
        "pdf",
        "doc",
        "docx",
        "xls",
        "xlsx",
        "ppt",
        "pptx",
        "odt",
        "ods",
        "odp",
        "ps",
        "pages",
        "key",
        "numbers",
        # images (scrapy reference)
        "mng",
        "pct",
        "bmp",
        "gif",
        "jpg",
        "jpeg",
        "png",
        "pst",
        "psp",
        "tif",
        "tiff",
        "ai",
        "drw",
        "dxf",
        "eps",
        "svg",
        "cdr",
        "ico",
        "webp",
        # audio
        "mp3",
        "wma",
        "ogg",
        "wav",
        "ra",
        "aac",
        "mid",
        "au",
        "aiff",
        "flac",
        # video
        "3gp",
        "asf",
        "asx",
        "avi",
        "mov",
        "mp4",
        "mpg",
        "qt",
        "rm",
        "swf",
        "wmv",
        "webm",
        "mkv",
        # web (not HTML)
        "css",
        "js",
        "rss",
        "atom",
    }
)

# ── 资源阻断与 Chromium 启动 ─────────────────────────────────────────────────
# 使用者: browser/browser_pool.py start() + route / crawler/engine.py run()
# 资源阻断已收编入下载子系统 (Download)。启用下载后由下载预设的 resource_type
# 门控 (Layer 1) 统一控制放行/阻断；未启用下载时全部阻断（向后兼容）。
BLOCKED_RESOURCE_TYPES = frozenset({"image", "font", "media", "websocket", "prefetch", "manifest"})
CHROMIUM_LAUNCH_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
    "--log-level=3",  # 仅 ERROR 级日志，防止代理凭据在 Chromium 调试输出中泄漏
)
__all__ = [
    # 解析规则引擎
    "MAX_RULES_TOTAL",
    "MAX_FALLBACK_DEPTH",
    "MAX_FIELDS_PER_RULE",
    "MAX_FIELD_NAME_LENGTH",
    "MULTIPLE_MAX_ITEMS",
    "SELECTOR_TIMEOUT_PER_RULE",
    "MAX_RULE_FILE_SIZE",
    "MAX_RULES_CACHE_SIZE",
    "MAX_JSON_DEPTH",
    "RULES_TMP_MAX_AGE_HOURS",
    "SOURCE_PRIORITY",
    "RULE_NAME_MAX_LENGTH",
    "RULE_NAME_PATTERN",
    "ATTR_NAME_PATTERN",
    "CURRENCY_SYMBOLS",
    "TRANSFORM_MEMORY_MULTIPLIER",
    "MANIFEST_MAX_BYTES",
    "SOURCE_DOWNLOAD_TIMEOUT",
    "SOURCE_DAILY_UPDATE_LIMIT",
    "SOURCE_DEGRADED_COOLDOWN",
    "DOWNLOAD_CONCURRENCY_GLOBAL",
    "DOWNLOAD_CONCURRENCY_PER_SOURCE",
    "MAX_REDIRECTS",
    "MAX_SOURCE_URL_LENGTH",
    # Worker 与队列
    "WORKER_IDLE_SLEEP",
    "RUN_EMPTY_QUEUE_WAIT",
    "RUN_EMPTY_QUEUE_CONFIRM",
    # 关闭与生命周期
    "SHUTDOWN_PENDING_TIMEOUT",
    "SHUTDOWN_EXECUTOR_TIMEOUT",
    "SHUTDOWN_ASYNCGEN_TIMEOUT",
    "IN_FLIGHT_DRAIN_TIMEOUT",
    # Sitemap 与 robots.txt
    "SITEMAP_FETCH_TIMEOUT",
    "SITEMAP_MAX_CONTENT_SIZE",
    "SITEMAP_MAX_DECOMPRESSED",
    "ROBOTS_FETCH_TIMEOUT",
    "ROBOTS_FETCH_MAX_CONCURRENT",
    "ROBOTS_MAX_SIZE",
    # 浏览器上下文与页面管理
    "PAGE_CREATE_RETRIES",
    "PAGE_CREATE_BACKOFF",
    "PAGE_CREATE_TIMEOUT",
    "PAGE_CLOSE_TIMEOUT",
    "ABOUT_BLANK_ASYNCIO_TIMEOUT",
    "SLOT_CREATE_RETRIES",
    "SLOT_CREATE_BACKOFF",
    "CONTEXT_CREATE_TIMEOUT",
    "RELEASE_UNROUTE_TIMEOUT",
    "BROWSER_LAUNCH_TIMEOUT",
    "HARD_CLEANUP_TIMEOUT",
    # 纵深防御超时
    "CONTENT_READ_TIMEOUT",
    "PROCESS_URL_TIMEOUT",
    "FETCH_PROCESSOR_OVERHEAD",
    "DOMAIN_CONCURRENCY_ACQUIRE_TIMEOUT",
    # 卡死检测与上下文健康
    "WORKER_STUCK_TIMEOUT",
    "STUCK_IN_FLIGHT_TIMEOUT",
    "CONTEXT_HEALTH_CHECK_INTERVAL",
    "BROWSER_GOTO_BUFFER_S",
    "BROWSER_RESTART_TIMEOUT_MULT",
    # 代理健康管理
    "PROXY_FAILURE_THRESHOLD",
    "PROXY_COOLDOWN",
    "PROXY_COOLDOWN_MAX",
    "PROXY_HALF_OPEN_MIN_DURATION",
    "PROXY_HALF_OPEN_MAX_FAILURES",
    "PROXY_DECAY_SECONDS",
    "PROXY_PROBE_INTERVAL",
    "PROXY_PROBE_TIMEOUT",
    "PROXY_SCORE_WINDOW",
    "PROXY_SCORE_SUCCESS_DECAY",
    "PROXY_HEALTH_BAR_REFRESH",
    # 域管理
    "DOMAIN_CLEANUP_AGE",
    "DNS_CACHE_TTL",
    # 后台维护周期
    "RETRY_MONITOR_INTERVAL",
    "RETRY_MONITOR_BATCH",
    "RATE_LIMITER_CLEANUP_INTERVAL",
    "CONCURRENT_LIMITER_CLEANUP_INTERVAL",
    "RESOURCE_MONITOR_INTERVAL",
    "HASH_CLEANUP_INTERVAL",
    "HASH_MAX_AGE",
    # 诊断
    "HTTP_READ_LINE_TIMEOUT",
    # 连接器
    "CONNECTOR_LIMIT",
    "CONNECTOR_LIMIT_PER_HOST",
    # 日志
    "FILE_LOG_MAX_BYTES",
    "FILE_LOG_BACKUP_COUNT",
    "MAX_LOG_ITEMS",
    "MAX_ERROR_MESSAGE_LENGTH",
    # GUI 控件物理极限
    "QSPINBOX_MAX",
    "QDOUBLESPINBOX_MAX",
    "QLINEEDIT_MAX",
    # 下载候选扩展名
    "DOWNLOAD_EXTENSIONS",
    # 资源阻断与 Chromium 启动
    "BLOCKED_RESOURCE_TYPES",
    "CHROMIUM_LAUNCH_ARGS",
]
