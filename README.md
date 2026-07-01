<p align="center">
  <h1 align="center">AstroCrawl</h1>
  <p align="center"><strong>专业级异步网页爬虫 — GUI + CLI 双界面</strong></p>
  <p align="center"><em>Professional async web crawler — GUI + CLI dual interface</em></p>
</p>

<p align="center">
  <img src="https://img.shields.io/pypi/v/astrocrawl" alt="PyPI">
  <img src="https://img.shields.io/pypi/pyversions/astrocrawl" alt="Python">
  <img src="https://img.shields.io/pypi/dm/astrocrawl" alt="Downloads">
  <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License">
  <img src="https://img.shields.io/badge/platform-Linux%20|%20macOS%20|%20Windows-lightgrey" alt="Platform">
</p>

---

# 中文文档

AstroCrawl 是基于 **Playwright 无头 Chromium** 的全功能异步网页爬虫，约 28,500 行 Python，114 源文件，内置 3 个 AI Provider，103 测试文件 3,659 测试。支持 JavaScript 渲染、robots.txt 遵从（RFC 9309）、Sitemap 自动发现、代理轮换、内容去重、崩溃恢复，以及声明式 CSS 选择器提取规则引擎和多 Provider AI 基础设施。提供 PySide6 GUI 图形界面和功能完整的 CLI 命令行两种使用方式。

## 快速开始

```bash
# PyPI 安装（推荐）
pip install astrocrawl                     # 核心爬虫 + CLI
pip install astrocrawl[gui]                # 含图形界面
pip install astrocrawl[openai]             # 含 OpenAI Provider
pip install astrocrawl[full,gui,fast]      # 全家桶
playwright install chromium
astrocrawl https://example.com -d 2
```

```bash
# 源码安装（开发者）
# 1. 克隆仓库
git clone https://github.com/Etoileint/AstroCrawl.git
cd AstroCrawl

# 2. 创建虚拟环境（推荐）
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
# .venv\Scripts\Activate.ps1   # Windows PowerShell

# 3. 安装主包 + 可选依赖
pip install -e ".[fast,monitor,yaml]"

# 4. 安装 AI Provider（可选）
pip install "astrocrawl[openai]"

# 5. 安装 Chromium 浏览器
playwright install chromium

# 6. 运行
astrocrawl https://example.com -d 2   # CLI 模式
astrocrawl                              # GUI 模式（无参数）
```

## 核心能力

| 能力 | 说明 |
|------|------|
| **JavaScript 渲染** | Playwright 无头 Chromium，完整执行页面 JS，支持 CDP 健康检测 |
| **robots.txt 遵从** | RFC 9309 完整实现 — data/policy 分离，始终获取 robots.txt，Disallow 拦截按需开关，Crawl-Delay 独立控制 |
| **站点地图发现** | 自动从 robots.txt / 默认路径发现 Sitemap，递归解析 Sitemap Index，UrlGate 统一准入 |
| **结构化提取** | 声明式 CSS 选择器规则引擎 — MatchScope 4 级精确度，字段级提取 + 5 变换流水线，3 层规则源（用户/远程/预置） |
| **Schema.org 提取** | 零成本自动解析 JSON-LD 和 Microdata 结构化数据，所有页面默认执行 |
| **AI 辅助规则生成** | 双路径：外部 AI ChatML 粘贴导入 / GUI 一键 API 调用，零样本 Prompt，共享 3 级 HTML 预处理 |
| **AI 多 Provider 底座** | 3 Provider（OpenAI/Anthropic/Google）、多 Profile 管理、C-mode 上下文选择、流式/工具调用/嵌入 |
| **代理轮换** | 4 种代理模式 + 3 级断路器 + TCP 主动探测 + DomainPathMemory 双缓存 + ProxyProfile 配置档案 |
| **双层速率控制** | 每域名随机延迟 + 同域名并发限制，非阻塞锁设计 |
| **资源阻断** | 请求级拦截非必要资源类型（image/font/media/websocket/prefetch/manifest），CSS/JS 不禁用 |
| **崩溃恢复** | SQLite WAL 全量持久化 — in_flight 恢复、边界链接自动展开/暂存、链接图自愈 |
| **内容去重** | 两层独立：URL 去重 + 稳健哈希内容去重（头/中/尾采样 MD5，24h TTL） |
| **深度控制** | UrlGate 统一准入（对标 Heritrix CrawlScope），超限 URL 自动暂存边界链接 |
| **认证支持** | HTTP Basic Auth / Bearer Token / Cookie 文件导入 / 自定义 HTTP 头 |
| **双界面** | PySide6 GUI（3 主题模式 + 双语 en↔zh_CN） + 功能完整的 CLI |
| **健康监控** | 统一 HealthChecked 协议 + A/B/C 三级分类调度 + HTTP /health 端点 |
| **三重诊断** | SIGUSR1 asyncio 任务转储 + HTTP /health 端点 + 卡死/熔断自动转储 |
| **通知** | 爬取完成 Webhook POST（JSON 报告） |

## 安装

### 系统要求

- **Python** 3.12 或更高版本
- **操作系统** Linux / macOS / Windows
- **内存** 建议 2GB 以上（Chromium 每上下文约 150–300MB）

### PyPI 安装（推荐）

```bash
pip install astrocrawl                     # 核心爬虫 + CLI
pip install astrocrawl[gui]                # 含图形界面
pip install astrocrawl[openai]             # 含 OpenAI Provider
pip install astrocrawl[full,gui,fast]      # 全家桶
playwright install chromium
astrocrawl --help
```

### 源码安装（开发者）

**1. 克隆仓库**

```bash
git clone https://github.com/Etoileint/AstroCrawl.git
cd AstroCrawl
```

**2. 创建虚拟环境（推荐）**

```bash
python -m venv .venv

# 激活（Linux / macOS）:
source .venv/bin/activate

# 激活（Windows PowerShell）:
.venv\Scripts\Activate.ps1

# 激活（Windows CMD）:
.venv\Scripts\activate.bat
```

**3. 安装主包**

```bash
# 基础安装（仅核心依赖）
pip install -e .

# 推荐安装（含加速 + 监控 + YAML 支持）
pip install -e ".[fast,monitor,yaml]"

# 开发者安装（含测试 + 代码质量工具）
pip install -e ".[fast,monitor,yaml,dev]"
```

**4. 安装 AI Provider（可选，需要 AI 规则生成功能时安装）**

> AI Provider 已内置在主包中，只需额外安装对应的 SDK。

```bash
pip install "astrocrawl[openai]"         # OpenAI（GPT-4o / GPT-5）
pip install "astrocrawl[anthropic]"       # Anthropic（Claude 系列）
pip install "astrocrawl[google]"          # Google（Gemini 系列）
pip install "astrocrawl[full]"            # 全部三个 Provider
```

安装后需设置对应的 API 密钥环境变量，参见 `.env.example`。Provider 通过 `importlib.metadata` entry point 自动发现，安装即可用。

**5. 安装 GUI 依赖（可选）**

```bash
pip install astrocrawl[gui]
```

**6. 安装 Chromium 浏览器**

```bash
playwright install chromium
```

**7. 验证安装**

```bash
astrocrawl --help    # 应显示 CLI 帮助信息
astrocrawl            # 应启动 GUI 窗口
```

### 可选依赖

| 组 | 包含 | 作用 |
|----|------|------|
| `openai` | openai | AI 功能（OpenAI Provider） |
| `anthropic` | anthropic | AI 功能（Anthropic Provider） |
| `google` | google-genai | AI 功能（Google Provider） |
| `full` | openai, anthropic, google-genai | 全部 AI Provider |
| `fast` | orjson | JSON 加速 |
| `gui` | PySide6 | GUI 图形界面 |
| `dev` | pytest, mypy, ruff | 测试与代码质量 |
| `monitor` | psutil | 资源监控 |
| `yaml` | pyyaml | YAML 配置文件支持 |

## CLI 命令行

### 基本语法

```
astrocrawl [URLS...] [选项]
```

### 常用选项

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `-d, --depth` | 2 | 爬取深度（0=仅起始页） |
| `-c, --concurrency` | 8 | Worker 并发数 |
| `-o, --output` | `crawler_output.jsonl` | 输出文件路径 |
| `-p, --proxy` | — | 代理池 JSON 文件 |
| `--same-domain` | False | 仅爬取同域名页面 |
| `--no-robots` | False | 忽略 robots.txt |
| `--config` | — | JSON/YAML/TOML 配置文件 |
| `--set KEY=VALUE` | — | 通用配置覆盖（对标 scrapy `-s`），自动类型解析 |
| `--max-pages` | 0 | 最大页面数（0=无限制） |
| `--max-runtime` | 0 | 最大运行秒数（0=无限制） |
| `--sitemap / --no-sitemap` | True | Sitemap 自动发现 |
| `--contact` | "" | 联系方式（附加到 UA） |
| `--log-level` | INFO | DEBUG / INFO / WARNING / ERROR |

配置优先级：`CLI 显式 flag > --set 覆盖 > 环境变量 > --config 文件 > Preferences 全局设置 > 默认值`

### 使用示例

```bash
# 基础爬取
astrocrawl https://example.com -d 2

# 多起始 URL + 同域名限制
astrocrawl https://example.com/page1 https://example.com/page2 -d 3 --same-domain

# 使用代理池
astrocrawl https://example.com -d 3 -p proxies.json

# 使用配置文件
astrocrawl --config my_config.json

# --set 配置覆盖
astrocrawl https://example.com -d 3 --set concurrency=16 --set max_total_pages=1000

# 最大 500 页 + 跳过重复链接
astrocrawl https://example.com -d 3 --max-pages 500 --skip-duplicate-links

# 限制运行 30 分钟
astrocrawl https://example.com -d 3 --max-runtime 1800

# 声明联系方式（推荐做法）
astrocrawl https://example.com -d 2 --contact "admin@example.com"

# 规则管理
astrocrawl rules list                         # 列出所有已加载规则
astrocrawl rules validate --name <名称>       # 验证指定规则
astrocrawl rules import <rule.json>           # 导入用户规则
astrocrawl rules enable --all                 # 批量启用所有规则
astrocrawl rules disable --all --dry-run      # 预览批量禁用

# AI 规则生成
astrocrawl rules generate --url <URL> --html-file <path> --fields a,b,c

# 远程规则源管理
astrocrawl source list                        # 列出已配置规则源
astrocrawl source update --all                # 更新所有远程规则源

# AI Profile 管理
astrocrawl ai profile list                    # 列出所有 AI Profile
astrocrawl ai profile add <name>              # 添加 AI Profile
astrocrawl ai profile test <name>             # 测试连接

# 代理 Profile 管理
astrocrawl proxy profile list                 # 列出所有代理 Profile
astrocrawl proxy profile add <name>           # 添加代理 Profile
```

> 完整 CLI 参考见 `astrocrawl --help`。

## GUI 图形界面

```bash
astrocrawl   # 无参数自动启动 GUI
```

GUI 基于 PySide6 (Qt6) Fusion 风格，内置 Qt 中文翻译（`qtbase_zh_CN.qm`）。主要功能块：

| 功能模块 | 说明 |
|---------|------|
| **起始 URL 编辑器** | 实时格式校验，多 URL 添加/删除 |
| **基本配置** | 深度、并发、输出路径、代理文件 |
| **高级设置对话框** | 5 Tab：通用(50 配置项)、全局(7 全局设置)、AI、代理、路由 |
| **Worker 状态条** | 推模式脉动渐变条，4-stop 双周期动画，速度随活跃 Worker 数动态变化 |
| **代理健康条** | 每代理独立健康状态可视化 |
| **按层进度条** | 各深度层 planned/processed 进度追踪 |
| **实时分类统计** | outcome 分布、域名统计、规则命中统计 |
| **爬取完成弹窗** | 完整统计明细表格 |
| **主题切换** | 浅色/深色/自定义 3 模式，15 颜色令牌可定制，持久化保存 |
| **规则管理对话框** | 3 Tab：规则列表（MVC 表格 + 搜索/启用/禁用/编辑/删除/验证）+ 自定义规则 + 远程源管理 |
| **AI 规则生成** | 双路径：外部 ChatML 粘贴导入 / 一键 API 调用，含 tiktoken Token 统计 |
| **AI Profile 管理** | 多 Profile CRUD + C-mode 上下文选择 + Test Connection 连接验证 + 动态模型列表 |
| **代理 Profile 管理** | 代理端点组合 + 消费者路由配置，Dirty Check 变更检测 |
| **配置文件保存/加载** | JSON/YAML/TOML 格式支持 |
| **脏检查** | AI Profile / Proxy Profile 编辑对话框未保存变更检测，Cancel 时弹出确认 |

> GUI 完整操作说明见 [`docs/guides/developer-guide.md`](docs/guides/developer-guide.md)。

## 配置

### 配置文件示例（JSON）

```json
{
    "concurrency": 5,
    "domain_min_delay": 3.0,
    "domain_max_delay": 10.0,
    "max_total_pages": 5000,
    "max_retries": 5,
    "robots_respect": true,
    "use_sitemap": true,
    "skip_non_essential_resources": true,
    "exclude_patterns": [
        "^https?://[^/]+/tag/",
        "^https?://[^/]+/category/"
    ],
    "custom_headers": [
        "Accept-Language: zh-CN,zh;q=0.9"
    ]
}
```

### 配置优先级

```
CLI 显式 flag > --set 覆盖 > 环境变量 > --config 文件 > Preferences 全局设置 > 默认值
```

### 环境变量

`ASTROCRAWL_CONCURRENCY` · `ASTROCRAWL_USER_AGENT` · `ASTROCRAWL_MAX_PAGES` · `ASTROCRAWL_MAX_RUNTIME` · `ASTROCRAWL_DB_PATH` · `ASTROCRAWL_LOG_LEVEL` · `ASTROCRAWL_LOG_FILE` · `ASTROCRAWL_CONTACT`

### 主要配置项

`CrawlerConfig` 共 50 字段，不可变冻结数据类（`frozen=True`），修改通过 `replace()` 方法。跨会话全局设置由 `GlobalSettings`（7 字段）独立管理，通过 `Preferences` 持久化，引擎启动时注入。

| 分类 | 关键字段 | 默认值 |
|------|---------|--------|
| **浏览器** | `page_timeout`, `viewport_width/height`, `user_agent`, `page_pool_size_per_context` | 20000ms, 1280×720, auto, 2 |
| **并发** | `concurrency`, `domain_max_concurrency`, `domain_min/max_delay`, `max_retries` | 8, 3, 1.0–5.0s, 3 |
| **存储** | `output_buffer_size`, `max_text_length`, `db_path` | 1MB, 500000, auto |
| **robots.txt** | `robots_respect`, `robots_user_agent`, `robots_cache_ttl` | True, "AstroCrawl", 3600s |
| **Sitemap** | `use_sitemap`, `sitemap_fetch_concurrency`, `sitemap_max_recursion` | True, 10, 2 |
| **链接** | `follow_nofollow`, `respect_meta_robots`, `skip_duplicate_links` | True, True, False |
| **认证** | `auth_basic_user/pass`, `auth_bearer_token`, `cookies_file` | — |
| **过滤** | `exclude_patterns`, `tracking_params`, `custom_headers` | [], 10 defaults, [] |
| **限制** | `max_total_pages`, `max_runtime_seconds`, `queue_hard_maxsize` | 0, 0, 50000 |
| **通知** | `webhook_url` | — |
| **代理** | `proxy_mode` | direct_only |
| **规则** | `rules_sources` | [] |
| **资源** | `skip_non_essential_resources` | True |
| **全局** | `output_gzip`, `rules_dirs`, `rules_auto_update`, `trace_rules`, `clear_context_cookies`, `log_level`, `rules_dirs_enabled` | GlobalSettings, see below |


## 代理

代理模块由 7 文件组成的三层架构：

```json
[
    "http://user:pass@proxy1.example.com:8080",
    "http://proxy2.example.com:3128",
    "socks5://proxy3.example.com:1080"
]
```

```bash
astrocrawl https://example.com -d 3 -p proxies.json
```

**代理模式**：`direct_only`（默认，直连不代理）| `prefer_proxy`（优先代理，不可用时退直连）| `prefer_direct`（优先直连，失败时切代理）| `proxy_only`（强制代理，无代理时启动即报错）

**核心机制**：
- **ProxyManager（SWRR 负载均衡）**：Smooth Weighted Round-Robin 分配代理到浏览器上下文槽位
- **ProxyHealthTracker（3 级断路器）**：CLOSED → OPEN（3 次连续失败熔断，30s 冷却）→ HALF_OPEN（15s 考察窗口）→ CLOSED；再次熔断冷却 ×1.5（最大 120s）
- **TCP 主动探测**：后台 asyncio 循环周期性 TCP connect 探测 OPEN 状态代理，自动恢复
- **DomainPathMemory（双缓存）**：按域名记忆代理/直连决策，Phase 0 快捷路径，TTL 3600s
- **ProxySession（组合根 + DI）**：异步上下文管理器，组合 ProxyManager + ProxyHealthTracker + 后台探活循环，通过 DI 注入 BrowserPool/aiohttp/AI/Preview 等消费者
- **ProxyFailureClassifier（SSOT）**：将 Playwright/网络错误映射为 4 种重试策略 — ROTATE_PROXY / REPLACE_CONTEXT / TRANSIENT / FATAL
- **Consumer Routing**：`PROXY_CONSUMERS` 注册表（preview/ai/source），GUI `_RouteSettingsPage` 为每消费者配置 Profile | Node 路由
- **ProxyProfile（配置档案）**：4 字段 frozen dataclass + UUID 身份，完整 CRUD 内置于 Preferences，GUI 表格管理 + 编辑器
- **缺代理启动门控**：`proxy_only`/`prefer_proxy`/`prefer_direct` 无代理时直接 `ConfigError`，防止静默降级

## 输出格式

### JSONL（内容输出）

```json
{"url": "https://example.com/page1", "depth": 1, "text": "页面文本...", "title": "Example Page", "timestamp": 1714521600.123}
```

结构化提取模式下，额外包含 `extraction_type` 和 `fields`：

```json
{"url": "https://example.com/product/1", "depth": 2, "text": "", "title": "商品页标题", "timestamp": 1714521600.456, "extraction_type": "example_product", "fields": {"product_title": "商品名", "price": "99.00"}}
```

所有页面默认包含 `schema_org` 字段（JSON-LD / Microdata 自动解析）。仅写入 `ok` 和 `truncated` 状态的页面。可选 GZip 压缩（默认开启）。配套生成 `<output>.report.json` 统计报告。

### 统计报告（摘要）

```json
{
    "outcome_summary": {"ok": 420, "duplicate": 30, "fetch_error": 8},
    "domain_stats": [{"domain": "example.com", "ok": 420, "avg_ms": 2340.5}],
    "depth_layers": {"0": {"processed": 1, "planned": 1}},
    "duration_seconds": 930.5
}
```


## 崩溃恢复

使用相同的输出路径再次启动，自动续爬。

```bash
astrocrawl https://example.com -d 3 -o data.jsonl
# 中断后…
astrocrawl https://example.com -d 3 -o data.jsonl   # 自动续爬
```

恢复逻辑：in_flight URL 自动重新入队、进度层从持久化 meta 表恢复、深度变化时边界链接自动展开/暂存、自愈检测从链接图找回丢失子链接、DB 可重试 URL 回收。

## 提取规则引擎

声明式 CSS 选择器结构化提取系统对标 Zyte/Hext 的页面类型模型：

- **规则结构**：`ExtractionRule`（名称 + 域名 + url_pattern + fields[选择器/transform] + test_urls）
- **匹配流程**：`RuleSnapshot` 全量快照 → by_domain 索引 → `MatchScope` 4 级精确度排序 → `RuleMatchCache` 域名级缓存
- **提取流程**：CSS 选择器（text/attr/html，支持 multiple 数组 + fallback 链） → `RuleTransform` 5 变换（strip/strip_currency/regex/replace/join） → 结构化输出
- **规则源**：3 层（用户 > 远程 > pip预置 > default），远程源 HTTPS-only + SHA256 校验
- **HTML 预处理**：3 级清洗（OFF/CANONICAL/STRICT），AI 规则生成前自动执行
- **安全设计**：re2 硬依赖（线性时间，ReDoS 免疫）、3 层验证模型（L2 导入预览 → L1 持久化门 → L0 加载兜底）、DNS rebinding 硬阻断、Unicode 控制字符清洗

## AI 多 Provider 架构

领域无关的通用 AI 底座，`astrocrawl/ai/` 16 文件约 2,440 行，零 Provider SDK 导入：

| 组件 | 说明 |
|------|------|
| **AIClient 门面** | 统一 API — `chat()`/`achat()`/`chat_stream()`/`achat_stream()`/`embed()`，异步上下文管理器 |
| **Provider 注册表** | `importlib.metadata` entry point 自动发现，工厂模式创建，3 个内置 Provider 包 |
| **RateLimiter** | TokenBucket + BoundedSemaphore，sync/async 共享预算 |
| **UsageTracker** | 会话级 TokenUsage 累加器 |
| **AIHook 可观测性** | OTel 对齐 — on_request/on_response/on_error/on_retry，4 生命周期钩子 |
| **StreamEvent** | Vercel AI SDK 对齐 5 事件判别联合 — StreamText/StreamToolCallStart/StreamToolCallDelta/StreamToolCall/StreamFinish |
| **Tool Calling** | 归一化 `ToolCall`，`arguments` 已解析为 dict |
| **Embeddings** | ISP 分离 `_SupportsEmbedding` 协议，OpenAI/Google Provider 支持 |
| **AIProfile** | 10 字段 frozen dataclass，多 Profile CRUD，持久化在 Preferences 中 |
| **C-mode 选择** | 每 AI 消费者模块独立选择并记忆 Profile，对标 Cursor/Continue.dev |
| **动态模型列表** | 约定发现 `list_models()`，GUI 异步拉取 + Refresh 按钮 |
| **异常体系** | 9 个 Provider 无关异常类 — AIAuthError/AIRateLimitError/AITimeoutError 等 |

## 架构

```
入口层   main.py → CLI (cli/main.py) / GUI (gui/*.py)
引擎层   AsyncCrawler — Processor Chain (8 processors) + WorkerSupervisor + LivenessTracker + UrlGate
规则层   rules/ (RuleSnapshot → match → CSS extract → transform) + ai/ (多Provider AI底座)
设施层   browser/ (BrowserPool Actor + ContextPool strategy + SlotPool mechanism + PagePool)
         network/ (aiohttp_retry_fetch + RobotsCache data/policy + RateLimiter×2 + SitemapDiscovery)
         storage/ (CrawlState SQLite WAL + AsyncJsonlWriter JSONL+GZip)
         proxy/   (ProxyManager SWRR + ProxyHealthTracker 3-tier CB + ProxySession DI + TCP probe)
工具层   utils/ (URL 规范化/脱敏 + HTML 解析/RobustHash + Logging logfmt + Preferences CRUD + 原子写入)
配置层   config.py (GlobalSettings 7 + CrawlerConfig 50 frozen dataclasses) + _constants.py + _types.py
         + _path_strategy.py + _retry_strategy.py + _startup.py + _packaged.py + _version.py
         + health.py + health_monitor.py (A/B/C 调度) + resilience.py (Fuse) + diagnostics.py (三重诊断)
```

核心原则：**引擎层不依赖任何 CLI 或 GUI 代码**，通过可选的 `CrawlerSignals` Qt 信号对象与 GUI 通信。Worker 通过 `FetchRequest` 消息模式与 BrowserPool Actor 交互，不直接接触 Playwright。关键设计范式：Mechanism/Strategy 分离、Data/Policy 分离、lock-free snapshot 读模式、`BEGIN IMMEDIATE` 事务、不可变配置、ISP 窄接口（PEP 544 Protocol）、Provider-agnostic 零 SDK 导入。

## 项目结构

```
AstroCrawl/
├── astrocrawl/                     # 主包（114 源文件，~28,500 行）
│   ├── [Kernel 16]               # __main__ main config _constants _types _version _path_strategy _retry_strategy
│   │                               _startup _packaged _json_compat health resilience diagnostics health_monitor
│   ├── crawler/                   # 引擎核心（8 文件）
│   │   ├── engine.py              # AsyncCrawler + Pipeline + _run_worker_loop + 8 processors
│   │   ├── supervisors.py         # WorkerSupervisor — OTP one_for_one
│   │   ├── liveness.py            # LivenessTracker — 心跳存活检测
│   │   ├── outcomes.py            # UrlOutcome (9 变体) + CrawlStats
│   │   ├── progress.py            # ProgressReporter（CLI stderr / GUI Qt 信号）
│   │   ├── signals.py             # CrawlerSignals 协议 + _StubSignals null-object
│   │   └── _url_gate.py           # UrlGate — 统一 URL 准入（6 AdmitResult 变体）
│   ├── browser/                   # 浏览器管理（10 文件）
│   │   ├── browser_pool.py        # BrowserPool Actor — K Chromium × N slots
│   │   ├── context_pool.py        # ContextPool — strategy 层
│   │   ├── _slot_pool.py          # SlotPool — mechanism 层，原子 swap
│   │   ├── page_pool.py           # PagePool — acquire/release/close
│   │   ├── navigation.py          # safe_goto — CDP + asyncio 双超时
│   │   ├── _domain_memory.py      # DomainPathMemory — 双缓存（TTL 3600s）
│   │   ├── _device_caps.py        # 设备 GPU 能力检测，SwiftShader fallback
│   │   ├── _preview.py            # PreviewBrowser — 纯 async 组件（headed Chromium）
│   │   └── _retry.py              # ProxyFailureClassifier SSOT（Playwright → RetryStrategy）
│   ├── network/                   # 网络层（5 文件）
│   │   ├── _fetch.py              # aiohttp_retry_fetch — 与 BrowserPool 策略等价的 aiohttp 重试引擎
│   │   ├── robots.py              # RobotsCache — RFC 9309, data/policy 分离
│   │   ├── sitemap.py             # SitemapDiscovery — 递归索引解析
│   │   └── throttling.py          # DomainTracker + DomainRateLimiter + DomainConcurrencyLimiter (机制/策略分离)
│   ├── storage/                   # 持久化（4 文件）
│   │   ├── db.py                  # CrawlState — 9 tables, BEGIN IMMEDIATE, crash recovery
│   │   ├── writer.py              # AsyncJsonlWriter — JSONL+GZip, periodic flush
│   │   └── _protocol.py           # CrawlStateProtocol (PEP 544)
│   ├── rules/                     # 提取规则引擎（15 文件）
│   │   ├── _schema.py             # RuleSchema, ExtractionRule, FieldSchema
│   │   ├── _loader.py             # RuleLoader — 3-tier source + dedup + RuleSnapshot
│   │   ├── _matcher.py            # RuleMatcher — by_domain index, MatchScope 4 级
│   │   ├── _extractor.py          # CSS selector extraction (text/attr/html)
│   │   ├── _transform.py          # RuleTransform (strip/strip_currency/regex/replace/join)
│   │   ├── _lifecycle.py          # RuleLifecycle — enable/disable/export/import
│   │   ├── _source.py             # SourceManager + sources.json CRUD
│   │   ├── _state.py              # RuleState — fcntl-locked rules_state.json
│   │   ├── _io.py                 # Rule file I/O — atomic write + lock + corruption recovery
│   │   ├── _ai.py                 # RuleGenerator — AI 辅助规则生成
│   │   ├── _template.py           # Prompt template loading
│   │   ├── _chatml.py             # ChatML 序列化 + tiktoken counting
│   │   ├── _html_preprocess.py    # HTML 3 级预处理 (off/canonical/strict)
│   │   └── _markdown.py           # Markdown code block stripping
│   ├── ai/                        # AI 基础设施（16 文件，~2,440 行）
│   │   ├── _client.py             # AIClient facade (chat/achat/stream/embed)
│   │   ├── _config.py             # AIConfig + GenerationParams + _resolve_params()
│   │   ├── _constraint.py         # OutputConstraint — Provider 无关结构化输出
│   │   ├── _types.py              # ChatMessage, ToolCall, StreamEvent (5-event DU)
│   │   ├── _provider.py           # _ChatProvider + _SupportsEmbedding 协议 (ISP)
│   │   ├── _provider_registry.py  # Entry point 发现 + 工厂 + list_installed_providers()
│   │   ├── _errors.py             # 9 异常类 (Provider-agnostic)
│   │   ├── _rate_limiter.py       # _TokenBucket + BoundedSemaphore
│   │   ├── _usage_tracker.py      # TokenUsage 累加器
│   │   ├── _observability.py      # AIHook 协议 + LoggingHook
│   │   ├── _profile.py            # AIProfile — 10-field frozen dataclass
│   │   └── providers/             # 内置 Provider（4 文件，~1,065 行）
│   │       ├── openai.py          # OpenAIClient + create_provider + list_models
│   │       ├── anthropic.py       # AnthropicClient + create_provider + list_models
│   │       └── google.py          # GoogleClient + create_provider + list_models
│   ├── gui/                       # GUI 界面（24 文件）
│   │   ├── main_window.py         # MainWindow — 中央控制器 + TitleBar
│   │   ├── crawl_session.py       # CrawlSession — MVP Presenter (QObject + QThread)
│   │   ├── thread.py              # CrawlerThread — QThread + asyncio 隔离
│   │   ├── theme.py               # ThemeManager — 15 颜色令牌，QPalette 传播
│   │   ├── theme_dialog.py        # ThemeDialog — light/dark/custom
│   │   ├── title_bar.py           # TitleBar — 自定义窗口 chrome
│   │   ├── worker_status_bar.py   # WorkerStatusBar — 脉动渐变，推模型信号
│   │   ├── proxy_health_bar.py    # ProxyHealthBar — per-proxy 可视化
│   │   ├── advanced_dialog.py     # AdvancedSettingsDialog — 5-tab 设置 (General/Global/AI/Proxy/Route)
│   │   ├── completion_dialog.py   # CompletionReportDialog — 统计明细
│   │   ├── rules_dialog.py        # RulesDialog — 3-tab 规则管理
│   │   ├── _preview_session.py    # PreviewSession — MVP Presenter (QObject + PreviewThread)
│   │   ├── _preview_panel.py      # PreviewPanel — 规则可视化预览非模态 Singleton
│   │   ├── _tokens.py             # Layout 常量
│   │   ├── _style.py              # ColumnDef + create_managed_table() + style utilities
│   │   ├── _delegates.py          # StatusColorDelegate + CheckboxDelegate
│   │   ├── _table_page.py         # _TableManagementPage + _FilterProxy (Template Method)
│   │   ├── _ai_profile_page.py    # _AIProfilePage + AIProfileEditDialog
│   │   ├── _proxy_endpoint_dialog.py  # ProxyEndpointEditDialog — 7 字段端点编辑器
│   │   ├── _proxy_profile_page.py     # _ProxyProfilePage + ProxyProfileEditDialog
│   │   ├── _route_settings_page.py    # _RouteSettingsPage — consumer→profile 路由
│   │   ├── _i18n.py               # GUI i18n — QTranslator 生命周期 (en↔zh_CN)
│   │   └── _animated_bar.py       # QTimer 驱动的动画条基类
│   ├── cli/                       # CLI 界面（3 文件）
│   │   ├── main.py                # argparse — crawl (22 flags) + rules (12) + source (7) + proxy (6) + ai (6)
│   │   └── _i18n.py               # CLI i18n — .ts 复用，tr() 函数
│   ├── proxy/                     # 代理模块（7 文件）
│   │   ├── _config.py             # ProxyType/ProxyAuth/ProxyEndpointSpec/ProxyProfile/ParsedProxy/ProxyConfig
│   │   ├── _consumers.py          # PROXY_CONSUMERS — 静态 consumer→display-name 注册表
│   │   ├── _hook.py               # ProxyHook Protocol + LoggingProxyHook (cold-path, sync)
│   │   ├── _probe.py              # ProbeResult + probe_one() — TCP 连通性预检
│   │   ├── _proxy.py              # ProxyManager (SWRR) + ProxyHealthTracker (3-tier CB)
│   │   └── _session.py            # ProxySession — 组合根 + 生命周期门面 (DI, async ctx mgr)
│   └── utils/                     # 工具（6 文件）
│       ├── url.py                 # URL 规范化 + 凭证脱敏
│       ├── html.py                # HTML 解析 + RobustHash (head-middle-tail sampling)
│       ├── logging.py             # Logfmt 配置 + Qt log bridge
│       ├── preferences.py         # Preferences — 16 data fields, AI/Proxy Profile CRUD, C-mode
│       └── _atomic.py             # POSIX 原子写入 (mkstemp → fsync → os.replace)
├── LICENSE                         # Apache 2.0
├── tests/                         # pytest 测试套件（103 test 文件, 3,659 tests）
│   ├── conftest.py                # 共享 + GUI fixtures
│   ├── _fakes.py / _fakes_gui.py  # 核心 / GUI 测试替身
│   ├── Kernel: test_types test_config test_constants test_version test_resilience test_health test_health_monitor
│   │           test_diagnostics test_startup test_packaged test_json_compat test_main_entry test_retry_strategy test_path_strategy
│   ├── Crawler: test_engine test_outcomes test_liveness test_supervisors test_progress test_signals test_url_gate
│   ├── Browser: test_browser_pool test_browser_navigation test_browser_page_pool test_browser_slot_pool test_browser_context_pool
│   │             test_browser_domain_memory test_proxy_classifier test_device_caps test_preview
│   ├── Network: test_robots test_sitemap test_sitemap_discovery test_throttling test_fetch
│   ├── Storage: test_db test_db_expanded test_writer
│   ├── Rules: test_rules_engine test_rules_lifecycle test_rules_loader test_rules_source test_rules_diagnostics test_rules_state
│   │           test_rules_io test_rules_browser_edge test_rules_markdown test_html_preprocess test_chatml
│   ├── AI: test_ai_client test_ai_errors test_ai_generation test_ai_profile test_ai_provider test_ai_rate_limiter test_ai_rules
│   │        test_ai_template test_ai_constraint + ai_openai/test_client ai_anthropic/test_client ai_google/test_client
│   ├── Proxy: test_proxy test_proxy_config test_proxy_session test_proxy_probe
│   ├── Utils: test_url test_html test_utils_expanded test_atomic test_preferences test_logging
│   ├── CLI: test_cli_main test_cli_rules test_cli_source test_cli_proxy test_cli_ai
│   └── GUI: test_gui_core test_gui_theme test_gui_mainwindow_data test_gui_dialogs test_gui_worker_viz test_gui_mainwindow_behavior
│             test_gui_rules_dialog test_gui_tokens test_gui_style test_gui_delegates test_gui_table_page test_gui_ai_profile
│             test_gui_animated_bar test_preview_session test_preview_panel test_gui_i18n test_gui_proxy_config
│             test_proxy_endpoint_dialog test_proxy_profile_page test_route_settings_page
├── docs/                          # 项目文档
│   └── guides/                    # developer-guide.md + gui-standards.md + terminology.md
├── pyproject.toml
└── README.md
```

## 文档

| 文档 | 内容 |
|------|------|
| [`docs/guides/developer-guide.md`](docs/guides/developer-guide.md) | 贡献指南 / 架构设计 / 开发参考 |
| [`docs/guides/gui-standards.md`](docs/guides/gui-standards.md) | GUI 编码规范（信号命名、主题令牌、Dirty Check） |

## 开发

```bash
pip install -e ".[dev]"

pytest                              # 全部测试（3,659）
pytest --cov=astrocrawl              # 带覆盖率
pytest -m "not gui"                 # 跳过 GUI 测试
mypy astrocrawl/                     # 类型检查（strict 模式）
ruff check astrocrawl/               # Lint（py312, line-length=120）
ruff format astrocrawl/              # 格式化
```

`from __future__ import annotations` 全局强制（Ruff FA100 + isort 检查），私有函数/类默认 `_` 前缀，`if TYPE_CHECKING:` 延迟导入。

## 技术栈

| 层 | 技术 |
|----|------|
| 语言 | Python 3.12+ |
| 浏览器自动化 | Playwright (Chromium headless) |
| HTTP 客户端 | aiohttp + TCPConnector |
| 数据库 | aiosqlite (WAL 模式) |
| HTML 解析 | BeautifulSoup4 + lxml |
| 正则引擎 | google-re2（线性时间，ReDoS 免疫，运行时硬依赖） |
| AI 客户端 | openai / anthropic / google-genai（多 Provider，entry point 自动发现） |
| GUI | PySide6 (Qt6 for Python, Fusion 风格, QTranslator zh_CN) |
| 配置 | Pydantic v2 + JSON/YAML/TOML |
| 重试 | 内置 ProxyFailureClassifier + aiohttp_retry_fetch |
| 测试 | pytest + pytest-asyncio + pytest-cov |

## 安全

- **原子写入** — POSIX 协议（mkstemp → write → fsync → os.replace → chmod 0o600），对标 SQLite WAL / PostgreSQL WAL / Git core.fsync
- **并发锁** — fcntl.flock 保护 rules_state.json 和 sources.json 读-改-写周期，对标 git `.git/index.lock`
- **DNS 重绑定硬阻断** — 12 个私有/保留 IP 段检查，阻止 SSRF + DNS rebinding 攻击
- **Unicode 清洗** — display_name/author/description 自动过滤 Bidi Override / C0 控制字符 / Interlinear Annotation，对标 Unicode TR36 + Git ident 校验
- **AI 注入防御** — 五层 OWASP LLM01 防御（URL 重建、字段验证、XML 边界、输出验证、用户确认流程）
- **提取层截断** — max_text_length 前移到 `_extract_value` 三个分支，UTF-8 字节感知截断，防 50MB `<div>` 内存放大
- **Transform 两道门** — 绝对值天花板 + 比例天花板，独立日志事件，各防不同攻击向量
- **URL 脱敏** — 日志中自动隐藏代理凭据和 15+ 种敏感参数（token, key, secret, jwt 等），源 URL 仅保留 scheme://netloc
- **Chromium 日志抑制** — `--log-level=3` 防止代理凭据泄漏到浏览器调试输出
- **API Key 保护** — AIProfile `__repr__` 仅显示前 8 字符 + "..."，API key 绝不出现在日志中
- **文件权限** — 输出报告 `chmod 600`，Preferences 文件 `chmod 0o600`
- **Cookie 校验** — 仅接受 `.json` 后缀，验证数组格式和条目结构
- **远程规则源** — HTTPS-only，SHA256 manifest 校验，按需下载
- **规则正则** — re2 硬依赖确保线性时间匹配，无 ReDoS 攻击面

## 许可证

Apache 2.0 © Etoileint

## 版本历史

| 版本 | 说明 |
|------|------|
| **v0.1.1** | PyPI README 更新 — 安装指南完善、badges 改为动态 |
| **v0.1.0** | 首次公开发布 — 模块化异步爬虫，GUI + CLI 双界面 |

---

# English Documentation

AstroCrawl is a full-featured async web crawler built on **Playwright headless Chromium** — ~28,500 lines of Python across 114 source files, 3 built-in AI providers, 103 test files with 3,659 tests. It supports JavaScript rendering, robots.txt compliance (RFC 9309), automatic sitemap discovery, proxy rotation, content deduplication, crash recovery, a declarative CSS-selector extraction rules engine, and a multi-provider AI infrastructure. Available as both a PySide6 GUI desktop app and a feature-complete CLI tool.

## Quick Start

```bash
# PyPI install (recommended)
pip install astrocrawl                     # Core crawler + CLI
pip install astrocrawl[gui]                # With GUI
pip install astrocrawl[openai]             # With OpenAI Provider
pip install astrocrawl[full,gui,fast]      # All-in-one
playwright install chromium
astrocrawl https://example.com -d 2
```

```bash
# Source install (developers)
# 1. Clone
git clone https://github.com/Etoileint/AstroCrawl.git
cd AstroCrawl

# 2. Create virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
# .venv\Scripts\Activate.ps1   # Windows PowerShell

# 3. Install main package + optional dependencies
pip install -e ".[fast,monitor,yaml]"

# 4. Install AI providers (optional)
pip install "astrocrawl[openai]"

# 5. Install Chromium browser
playwright install chromium

# 6. Run
astrocrawl https://example.com -d 2   # CLI mode
astrocrawl                              # GUI mode (no arguments)
```

## Core Capabilities

| Capability | Description |
|------|------|
| **JavaScript Rendering** | Playwright headless Chromium with full JS execution and CDP health checks |
| **robots.txt Compliance** | RFC 9309 — data/policy separated, robots.txt always fetched, Disallow enforcement togglable, Crawl-Delay independently controlled |
| **Sitemap Discovery** | Auto-discovery from robots.txt / default paths, recursive Sitemap Index parsing, UrlGate unified admission |
| **Structured Extraction** | Declarative CSS-selector rule engine — MatchScope 4-tier precision, field-level extraction + 5-transform pipeline, 3-tier rule sources (user/remote/pip) |
| **Schema.org Extraction** | Zero-cost auto-extraction of JSON-LD and Microdata structured data, executed for all pages |
| **AI-Assisted Rule Generation** | Dual-path: external AI ChatML paste+import / GUI one-click API call, zero-shot prompt, shared 3-tier HTML preprocessing |
| **AI Multi-Provider Foundation** | 3 providers (OpenAI/Anthropic/Google), multi-profile management, C-mode context selection, streaming/tool calling/embeddings |
| **Proxy Rotation** | 4 proxy modes + 3-tier circuit breaker + active TCP probing + DomainPathMemory dual-cache + ProxyProfile management |
| **Dual-Layer Rate Control** | Per-domain random delay + same-domain concurrency limit, non-blocking lock design |
| **Resource Blocking** | Request-level interception of non-essential resource types (image/font/media/websocket/prefetch/manifest), CSS/JS not blocked |
| **Crash Recovery** | Full SQLite WAL persistence — in_flight recovery, boundary link auto-expand/stash, link graph self-healing |
| **Content Deduplication** | Two independent layers: URL dedup + robust hash content dedup (head/mid/tail sampled MD5, 24h TTL) |
| **Depth Control** | UrlGate unified admission (modeled on Heritrix CrawlScope), overshoot URLs auto-stashed to boundary links |
| **Authentication** | HTTP Basic Auth / Bearer Token / Cookie file import / custom HTTP headers |
| **Dual Interface** | PySide6 GUI (3 theme modes + bilingual en↔zh_CN) + feature-complete CLI |
| **Health Monitoring** | Unified HealthChecked protocol + A/B/C tiered scheduling + HTTP /health endpoint |
| **Triple Diagnostics** | SIGUSR1 asyncio task dump + HTTP /health endpoint + auto-dump on stall/fuse-open |
| **Notifications** | Crawl-completion Webhook POST (JSON report) |

## Installation

### Requirements

- **Python** 3.12+
- **OS** Linux / macOS / Windows
- **Memory** 2GB+ recommended (~150–300MB per Chromium context)

### PyPI Install (Recommended)

```bash
pip install astrocrawl                     # Core crawler + CLI
pip install astrocrawl[gui]                # With GUI
pip install astrocrawl[openai]             # With OpenAI Provider
pip install astrocrawl[full,gui,fast]      # All-in-one
playwright install chromium
astrocrawl --help
```

### Source Install (Developers)

**1. Clone the repository**

```bash
git clone https://github.com/Etoileint/AstroCrawl.git
cd AstroCrawl
```

**2. Create virtual environment (recommended)**

```bash
python -m venv .venv

# Activate (Linux / macOS):
source .venv/bin/activate

# Activate (Windows PowerShell):
.venv\Scripts\Activate.ps1

# Activate (Windows CMD):
.venv\Scripts\activate.bat
```

**3. Install main package**

```bash
# Base install (core dependencies only)
pip install -e .

# Recommended (with speedup + monitoring + YAML support)
pip install -e ".[fast,monitor,yaml]"

# Developer install (with tests + code quality tools)
pip install -e ".[fast,monitor,yaml,dev]"
```

**4. Install AI Providers (optional, needed for AI rule generation)**

> AI providers are built into the main package. Only the corresponding SDK needs to be installed.

```bash
pip install "astrocrawl[openai]"          # OpenAI (GPT-4o / GPT-5)
pip install "astrocrawl[anthropic]"       # Anthropic (Claude series)
pip install "astrocrawl[google]"          # Google (Gemini series)
pip install "astrocrawl[full]"            # All three providers
```

After installation, set the corresponding API key environment variables — see `.env.example`. Providers are auto-discovered via `importlib.metadata` entry points.

**5. Install GUI dependencies (optional, for desktop GUI)**

```bash
pip install astrocrawl[gui]
```

**6. Install Chromium browser**

```bash
playwright install chromium
```

**7. Verify installation**

```bash
astrocrawl --help    # Should display CLI help
astrocrawl            # Should launch the GUI window
```

### Optional Dependencies

| Group | Includes | Purpose |
|----|------|------|
| `openai` | openai | AI features (OpenAI provider) |
| `anthropic` | anthropic | AI features (Anthropic provider) |
| `google` | google-genai | AI features (Google provider) |
| `full` | openai, anthropic, google-genai | All AI providers |
| `fast` | orjson | JSON speedup |
| `gui` | PySide6 | GUI desktop app |
| `dev` | pytest, mypy, ruff | Tests & code quality |
| `monitor` | psutil | Resource monitoring |
| `yaml` | pyyaml | YAML config file support |

## CLI

### Syntax

```
astrocrawl [URLS...] [options]
```

### Key Options

| Option | Default | Description |
|------|--------|------|
| `-d, --depth` | 2 | Crawl depth (0 = seed pages only) |
| `-c, --concurrency` | 8 | Number of concurrent workers |
| `-o, --output` | `crawler_output.jsonl` | Output file path |
| `-p, --proxy` | — | Proxy pool JSON file |
| `--same-domain` | False | Only crawl pages on the same domain |
| `--no-robots` | False | Ignore robots.txt |
| `--config` | — | JSON/YAML/TOML config file |
| `--set KEY=VALUE` | — | Generic config override (modeled on scrapy `-s`), auto type-coercion |
| `--max-pages` | 0 | Maximum pages (0 = unlimited) |
| `--max-runtime` | 0 | Maximum runtime in seconds (0 = unlimited) |
| `--sitemap / --no-sitemap` | True | Sitemap auto-discovery |
| `--contact` | "" | Contact info (appended to User-Agent) |
| `--log-level` | INFO | DEBUG / INFO / WARNING / ERROR |

Precedence: `CLI explicit flag > --set override > env vars > --config file > Preferences global settings > defaults`

### Examples

```bash
# Basic crawl
astrocrawl https://example.com -d 2

# Multiple seeds + same-domain restriction
astrocrawl https://example.com/page1 https://example.com/page2 -d 3 --same-domain

# With proxy pool
astrocrawl https://example.com -d 3 -p proxies.json

# With config file
astrocrawl --config my_config.json

# --set config overrides
astrocrawl https://example.com -d 3 --set concurrency=16 --set max_total_pages=1000

# Max 500 pages + skip duplicate links
astrocrawl https://example.com -d 3 --max-pages 500 --skip-duplicate-links

# 30-minute runtime cap
astrocrawl https://example.com -d 3 --max-runtime 1800

# Declare contact info (recommended)
astrocrawl https://example.com -d 2 --contact "admin@example.com"

# Rule management
astrocrawl rules list                         # List all loaded rules
astrocrawl rules validate --name <rulename>   # Validate a specific rule
astrocrawl rules import <rule.json>           # Import user rules
astrocrawl rules enable --all                 # Batch enable all rules
astrocrawl rules disable --all --dry-run      # Preview batch disable

# AI rule generation
astrocrawl rules generate --url <URL> --html-file <path> --fields a,b,c

# Remote rule source management
astrocrawl source list                        # List configured rule sources
astrocrawl source update --all                # Update all remote rule sources

# AI Profile management
astrocrawl ai profile list                    # List all AI profiles
astrocrawl ai profile add <name>              # Add an AI profile
astrocrawl ai profile test <name>             # Test connection

# Proxy Profile management
astrocrawl proxy profile list                 # List all proxy profiles
astrocrawl proxy profile add <name>           # Add a proxy profile
```

> Full CLI reference: `astrocrawl --help`.

## GUI

```bash
astrocrawl   # No arguments → launches GUI
```

The GUI is built on PySide6 (Qt6) Fusion style with built-in Qt Chinese translation (`qtbase_zh_CN.qm`). Key modules:

| Module | Description |
|--------|------|
| **Seed URL Editor** | Real-time format validation, multi-URL add/remove |
| **Basic Configuration** | Depth, concurrency, output path, proxy file |
| **Advanced Settings Dialog** | 5 tabs: General (50 config fields), Global (7 global settings), AI, Proxy, Route |
| **Worker Status Bar** | Push-model pulse gradient bar, 4-stop dual-cycle animation, speed varies with active worker count |
| **Proxy Health Bar** | Per-proxy health visualization |
| **Per-Layer Progress** | Planned/processed progress tracking per depth layer |
| **Real-Time Stats** | Outcome distribution, domain stats, rule hit stats |
| **Completion Dialog** | Detailed stats table on crawl completion |
| **Theme Switching** | Light/dark/custom 3 modes, 15 customizable color tokens, persisted |
| **Rules Management Dialog** | 3 tabs: rule list (MVC table + search/enable/disable/edit/delete/validate) + custom rules + remote sources |
| **AI Rule Generation** | Dual-path: external ChatML paste+import / one-click API call, with tiktoken token counting |
| **AI Profile Management** | Multi-profile CRUD + C-mode context selection + Test Connection verification + dynamic model list |
| **Proxy Profile Management** | proxy endpoint combos + consumer routing config, Dirty Check change detection |
| **Config Save/Load** | JSON/YAML/TOML format support |
| **Dirty Check** | AI Profile / Proxy Profile edit dialog unsaved-change detection, with confirmation prompt on Cancel |

> Full GUI guide: [`docs/guides/developer-guide.md`](docs/guides/developer-guide.md).

## Configuration

### Config File Example (JSON)

```json
{
    "concurrency": 5,
    "domain_min_delay": 3.0,
    "domain_max_delay": 10.0,
    "max_total_pages": 5000,
    "max_retries": 5,
    "robots_respect": true,
    "use_sitemap": true,
    "skip_non_essential_resources": true,
    "exclude_patterns": [
        "^https?://[^/]+/tag/",
        "^https?://[^/]+/category/"
    ],
    "custom_headers": [
        "Accept-Language: zh-CN,zh;q=0.9"
    ]
}
```

### Precedence

```
CLI explicit flag > --set override > env vars > --config file > Preferences global settings > defaults
```

### Environment Variables

`ASTROCRAWL_CONCURRENCY` · `ASTROCRAWL_USER_AGENT` · `ASTROCRAWL_MAX_PAGES` · `ASTROCRAWL_MAX_RUNTIME` · `ASTROCRAWL_DB_PATH` · `ASTROCRAWL_LOG_LEVEL` · `ASTROCRAWL_LOG_FILE` · `ASTROCRAWL_CONTACT`

### Key Config Fields

`CrawlerConfig` has 50 fields total — an immutable frozen dataclass (`frozen=True`), modified via the `replace()` method. Cross-session global settings are managed independently by `GlobalSettings` (7 fields), persisted via `Preferences` and explicitly injected into the engine.

| Category | Key Fields | Defaults |
|------|---------|--------|
| **Browser** | `page_timeout`, `viewport_width/height`, `user_agent`, `page_pool_size_per_context` | 20000ms, 1280×720, auto, 2 |
| **Concurrency** | `concurrency`, `domain_max_concurrency`, `domain_min/max_delay`, `max_retries` | 8, 3, 1.0–5.0s, 3 |
| **Storage** | `output_buffer_size`, `max_text_length`, `db_path` | 1MB, 500000, auto |
| **robots.txt** | `robots_respect`, `robots_user_agent`, `robots_cache_ttl` | True, "AstroCrawl", 3600s |
| **Sitemap** | `use_sitemap`, `sitemap_fetch_concurrency`, `sitemap_max_recursion` | True, 10, 2 |
| **Links** | `follow_nofollow`, `respect_meta_robots`, `skip_duplicate_links` | True, True, False |
| **Auth** | `auth_basic_user/pass`, `auth_bearer_token`, `cookies_file` | — |
| **Filters** | `exclude_patterns`, `tracking_params`, `custom_headers` | [], 10 defaults, [] |
| **Limits** | `max_total_pages`, `max_runtime_seconds`, `queue_hard_maxsize` | 0, 0, 50000 |
| **Notify** | `webhook_url` | — |
| **Proxy** | `proxy_mode` | direct_only |
| **Rules** | `rules_sources` | [] |
| **Resources** | `skip_non_essential_resources` | True |
| **Global** | `output_gzip`, `rules_dirs`, `rules_auto_update`, `trace_rules`, `clear_context_cookies`, `log_level`, `rules_dirs_enabled` | GlobalSettings, see below |


## Proxy

Proxy module consists of 7 files in a three-tier architecture:

```json
[
    "http://user:pass@proxy1.example.com:8080",
    "http://proxy2.example.com:3128",
    "socks5://proxy3.example.com:1080"
]
```

```bash
astrocrawl https://example.com -d 3 -p proxies.json
```

**Proxy Modes**: `direct_only` (default, no proxy) | `prefer_proxy` (proxy preferred, fallback to direct) | `prefer_direct` (direct preferred, fallback to proxy) | `proxy_only` (proxy required, startup error if none configured)

**Core Mechanisms**:
- **ProxyManager (SWRR load balancing)**: Smooth Weighted Round-Robin assigns proxies to browser context slots
- **ProxyHealthTracker (3-tier circuit breaker)**: CLOSED → OPEN (3 consecutive failures, 30s cooldown) → HALF_OPEN (15s probe window) → CLOSED; cooldown ×1.5 on re-trip (max 120s)
- **Active TCP Probing**: Background asyncio loop periodically TCP-connects to OPEN proxies for auto-recovery
- **DomainPathMemory (dual-cache)**: Per-domain proxy/direct decision memory, Phase 0 fast path, TTL 3600s
- **ProxySession (composite root + DI)**: Async context manager composing ProxyManager + ProxyHealthTracker + background probe loop, injected into BrowserPool/aiohttp/AI/Preview consumers
- **ProxyFailureClassifier (SSOT)**: Maps Playwright/network errors to 4 retry strategies — ROTATE_PROXY / REPLACE_CONTEXT / TRANSIENT / FATAL
- **Consumer Routing**: `PROXY_CONSUMERS` registry (preview/ai/source), GUI `_RouteSettingsPage` per-consumer Profile | Node routing
- **ProxyProfile**: 4-field frozen dataclass + UUID identity, full CRUD in Preferences, GUI table management + editor
- **Missing-Proxy Startup Guard**: `proxy_only`/`prefer_proxy`/`prefer_direct` raise `ConfigError` when no proxies configured, preventing silent degradation

## Output Format

### JSONL (Content Output)

```json
{"url": "https://example.com/page1", "depth": 1, "text": "Page text...", "title": "Example Page", "timestamp": 1714521600.123}
```

Under structured extraction mode, `extraction_type` and `fields` are added:

```json
{"url": "https://example.com/product/1", "depth": 2, "text": "", "title": "Product Page Title", "timestamp": 1714521600.456, "extraction_type": "example_product", "fields": {"product_title": "Product Name", "price": "99.00"}}
```

All pages include a `schema_org` field by default (JSON-LD / Microdata auto-extraction). Only pages with `ok` and `truncated` status are written. Optional GZip compression (on by default). A `<output>.report.json` stats report is generated alongside.

### Stats Report (Summary)

```json
{
    "outcome_summary": {"ok": 420, "duplicate": 30, "fetch_error": 8},
    "domain_stats": [{"domain": "example.com", "ok": 420, "avg_ms": 2340.5}],
    "depth_layers": {"0": {"processed": 1, "planned": 1}},
    "duration_seconds": 930.5
}
```


## Crash Recovery

Restart with the same output path to auto-resume.

```bash
astrocrawl https://example.com -d 3 -o data.jsonl
# After interruption…
astrocrawl https://example.com -d 3 -o data.jsonl   # Auto-resume
```

Recovery logic: in_flight URLs auto re-enqueued, depth layers restored from persistent meta table, boundary links auto-expand/stash on depth change, self-healing detection recovers lost child links from the link graph, DB retryable URL recovery via `peek_retryable`.

## Extraction Rules Engine

Declarative CSS-selector structured extraction system modeled on Zyte/Hext page-type model:

- **Rule Structure**: `ExtractionRule` (name + domains + url_pattern + fields[selector/transform] + test_urls)
- **Match Flow**: `RuleSnapshot` full snapshot → by_domain index → `MatchScope` 4-tier precision ranking → `RuleMatchCache` domain-level cache
- **Extract Flow**: CSS selector (text/attr/html, supporting multiple arrays + fallback chains) → `RuleTransform` 5 transforms (strip/strip_currency/regex/replace/join) → structured output
- **Rule Sources**: 3 tiers (user > remote > pip > default), remote sources HTTPS-only + SHA256 verification
- **HTML Preprocessing**: 3-tier cleaning (OFF/CANONICAL/STRICT), auto-executed before AI rule generation
- **Security by Design**: re2 hard dependency (linear-time, ReDoS-immune), 3-tier validation model (L2 import preview → L1 persistence gate → L0 load-time guard), DNS rebinding hard block, Unicode control char sanitization

## AI Multi-Provider Architecture

Domain-agnostic, general-purpose AI foundation — `astrocrawl/ai/` 16 files, ~2,440 lines, zero Provider SDK imports:

| Component | Description |
|------|------|
| **AIClient Facade** | Unified API — `chat()`/`achat()`/`chat_stream()`/`achat_stream()`/`embed()`, async context manager |
| **Provider Registry** | `importlib.metadata` entry point auto-discovery, factory pattern, 3 built-in provider packages |
| **RateLimiter** | TokenBucket + BoundedSemaphore, sync/async shared budget |
| **UsageTracker** | Session-level TokenUsage accumulator |
| **AIHook Observability** | OTel-aligned — on_request/on_response/on_error/on_retry, 4 lifecycle hooks |
| **StreamEvent** | Vercel AI SDK-aligned 5-event discriminated union — StreamText/StreamToolCallStart/StreamToolCallDelta/StreamToolCall/StreamFinish |
| **Tool Calling** | Normalized `ToolCall` with parsed `arguments: dict` |
| **Embeddings** | ISP-separated `_SupportsEmbedding` protocol, supported by OpenAI/Google providers |
| **AIProfile** | 10-field frozen dataclass, multi-profile CRUD, persisted in Preferences |
| **C-mode Selection** | Per-module independent profile selection with memory, modeled on Cursor/Continue.dev |
| **Dynamic Model List** | Convention-based `list_models()` discovery, GUI async fetch with Refresh button |
| **Exception Hierarchy** | 9 provider-agnostic exception classes — AIAuthError/AIRateLimitError/AITimeoutError/etc. |

## Architecture

```
Entry Layer   main.py → CLI (cli/main.py) / GUI (gui/*.py)
Engine Layer  AsyncCrawler — Processor Chain (8 processors) + WorkerSupervisor + LivenessTracker + UrlGate
Rules Layer   rules/ (RuleSnapshot → match → CSS extract → transform) + ai/ (Multi-provider AI foundation)
Infra Layer   browser/ (BrowserPool Actor + ContextPool strategy + SlotPool mechanism + PagePool)
              network/ (aiohttp_retry_fetch + RobotsCache data/policy + RateLimiter×2 + SitemapDiscovery)
              storage/ (CrawlState SQLite WAL + AsyncJsonlWriter JSONL+GZip)
              proxy/   (ProxyManager SWRR + ProxyHealthTracker 3-tier CB + ProxySession DI + TCP probe)
Utils Layer   utils/ (URL normalize/redact + HTML parse/RobustHash + Logging logfmt + Preferences CRUD + atomic writes)
Config Layer  config.py (GlobalSettings 7 + CrawlerConfig 50 frozen dataclasses) + _constants.py + _types.py
              + _path_strategy.py + _retry_strategy.py + _startup.py + _packaged.py + _version.py
              + health.py + health_monitor.py (A/B/C scheduling) + resilience.py (Fuse) + diagnostics.py (triple diagnostics)
```

Core principle: **the engine layer has zero dependency on CLI or GUI code**, communicating with the GUI via an optional `CrawlerSignals` Qt signals object. Workers interact with BrowserPool through asynchronous `FetchRequest` messages and never touch Playwright directly. Key design paradigms: Mechanism/Strategy separation, Data/Policy separation, lock-free snapshot reads, `BEGIN IMMEDIATE` transactions, immutable configuration, ISP narrow interfaces (PEP 544 Protocol), provider-agnostic zero SDK imports.

## Project Structure

```
AstroCrawl/
├── astrocrawl/                     # Main package (114 source files, ~28,500 lines)
│   ├── [Kernel 16]               # __main__ main config _constants _types _version _path_strategy _retry_strategy
│   │                               _startup _packaged _json_compat health resilience diagnostics health_monitor
│   ├── crawler/                   # Engine core (8 files)
│   │   ├── engine.py              # AsyncCrawler + Pipeline + _run_worker_loop + 8 processors
│   │   ├── supervisors.py         # WorkerSupervisor — OTP one_for_one
│   │   ├── liveness.py            # LivenessTracker — heartbeat-based worker liveness
│   │   ├── outcomes.py            # UrlOutcome (9 variants) + CrawlStats
│   │   ├── progress.py            # ProgressReporter (CLI stderr / GUI Qt signals)
│   │   ├── signals.py             # CrawlerSignals protocol + _StubSignals null-object
│   │   └── _url_gate.py           # UrlGate — unified URL admission (6 AdmitResult variants)
│   ├── browser/                   # Browser management (10 files)
│   │   ├── browser_pool.py        # BrowserPool Actor — K Chromium × N slots
│   │   ├── context_pool.py        # ContextPool — strategy layer
│   │   ├── _slot_pool.py          # SlotPool — mechanism layer, atomic swap
│   │   ├── page_pool.py           # PagePool — acquire/release/close
│   │   ├── navigation.py          # safe_goto — CDP + asyncio dual timeout
│   │   ├── _domain_memory.py      # DomainPathMemory — dual-cache (TTL 3600s)
│   │   ├── _device_caps.py        # Device GPU capability detection, SwiftShader fallback
│   │   ├── _preview.py            # PreviewBrowser — pure async component (headed Chromium)
│   │   └── _retry.py              # ProxyFailureClassifier SSOT (Playwright → RetryStrategy)
│   ├── network/                   # Network layer (5 files)
│   │   ├── _fetch.py              # aiohttp_retry_fetch — BrowserPool-equivalent retry engine for aiohttp
│   │   ├── robots.py              # RobotsCache — RFC 9309, data/policy separated
│   │   ├── sitemap.py             # SitemapDiscovery — recursive index parsing
│   │   └── throttling.py          # DomainTracker + DomainRateLimiter + DomainConcurrencyLimiter (mechanism/strategy)
│   ├── storage/                   # Persistence (4 files)
│   │   ├── db.py                  # CrawlState — 9 tables, BEGIN IMMEDIATE, crash recovery
│   │   ├── writer.py              # AsyncJsonlWriter — JSONL+GZip, periodic flush
│   │   └── _protocol.py           # CrawlStateProtocol (PEP 544)
│   ├── rules/                     # Extraction rules engine (15 files)
│   │   ├── _schema.py             # RuleSchema, ExtractionRule, FieldSchema
│   │   ├── _loader.py             # RuleLoader — 3-tier source + dedup + RuleSnapshot
│   │   ├── _matcher.py            # RuleMatcher — by_domain index, MatchScope 4-tier
│   │   ├── _extractor.py          # CSS selector extraction (text/attr/html)
│   │   ├── _transform.py          # RuleTransform (strip/strip_currency/regex/replace/join)
│   │   ├── _lifecycle.py          # RuleLifecycle — enable/disable/export/import
│   │   ├── _source.py             # SourceManager + sources.json CRUD
│   │   ├── _state.py              # RuleState — fcntl-locked rules_state.json
│   │   ├── _io.py                 # Rule file I/O — atomic write + lock + corruption recovery
│   │   ├── _ai.py                 # RuleGenerator — AI-assisted rule generation
│   │   ├── _template.py           # Prompt template loading
│   │   ├── _chatml.py             # ChatML serialization + tiktoken counting
│   │   ├── _html_preprocess.py    # HTML 3-tier preprocessing (off/canonical/strict)
│   │   └── _markdown.py           # Markdown code block stripping
│   ├── ai/                        # AI infrastructure (16 files, ~2,440 lines)
│   │   ├── _client.py             # AIClient facade (chat/achat/stream/embed)
│   │   ├── _config.py             # AIConfig + GenerationParams + _resolve_params()
│   │   ├── _constraint.py         # OutputConstraint — provider-agnostic structured output
│   │   ├── _types.py              # ChatMessage, ToolCall, StreamEvent (5-event DU)
│   │   ├── _provider.py           # _ChatProvider + _SupportsEmbedding protocols (ISP)
│   │   ├── _provider_registry.py  # Entry point discovery + factory + list_installed_providers()
│   │   ├── _errors.py             # 9 exception classes (provider-agnostic)
│   │   ├── _rate_limiter.py       # _TokenBucket + BoundedSemaphore
│   │   ├── _usage_tracker.py      # TokenUsage accumulator
│   │   ├── _observability.py      # AIHook protocol + LoggingHook
│   │   ├── _profile.py            # AIProfile — 10-field frozen dataclass
│   │   └── providers/             # Built-in providers (4 files, ~1,065 lines)
│   │       ├── openai.py          # OpenAIClient + create_provider + list_models
│   │       ├── anthropic.py       # AnthropicClient + create_provider + list_models
│   │       └── google.py          # GoogleClient + create_provider + list_models
│   ├── gui/                       # GUI interface (24 files)
│   │   ├── main_window.py         # MainWindow — central controller + TitleBar
│   │   ├── crawl_session.py       # CrawlSession — MVP Presenter (QObject + QThread)
│   │   ├── thread.py              # CrawlerThread — QThread + asyncio isolation
│   │   ├── theme.py               # ThemeManager — 15 color tokens, QPalette propagation
│   │   ├── theme_dialog.py        # ThemeDialog — light/dark/custom
│   │   ├── title_bar.py           # TitleBar — custom window chrome
│   │   ├── worker_status_bar.py   # WorkerStatusBar — pulse gradient, push-model signals
│   │   ├── proxy_health_bar.py    # ProxyHealthBar — per-proxy visualization
│   │   ├── advanced_dialog.py     # AdvancedSettingsDialog — 5-tab settings (General/Global/AI/Proxy/Route)
│   │   ├── completion_dialog.py   # CompletionReportDialog — stats detail
│   │   ├── rules_dialog.py        # RulesDialog — 3-tab rule management
│   │   ├── _preview_session.py    # PreviewSession — MVP Presenter (QObject + PreviewThread)
│   │   ├── _preview_panel.py      # PreviewPanel — rule visualization preview non-modal Singleton
│   │   ├── _tokens.py             # Layout constants
│   │   ├── _style.py              # ColumnDef + create_managed_table() + style utilities
│   │   ├── _delegates.py          # StatusColorDelegate + CheckboxDelegate
│   │   ├── _table_page.py         # _TableManagementPage + _FilterProxy (Template Method)
│   │   ├── _ai_profile_page.py    # _AIProfilePage + AIProfileEditDialog
│   │   ├── _proxy_endpoint_dialog.py  # ProxyEndpointEditDialog — 7-field endpoint editor
│   │   ├── _proxy_profile_page.py     # _ProxyProfilePage + ProxyProfileEditDialog
│   │   ├── _route_settings_page.py    # _RouteSettingsPage — consumer→profile routing
│   │   ├── _i18n.py               # GUI i18n — QTranslator lifecycle (en↔zh_CN)
│   │   └── _animated_bar.py       # QTimer-driven animated bar base class
│   ├── cli/                       # CLI interface (3 files)
│   │   ├── main.py                # argparse — crawl (22 flags) + rules (12) + source (7) + proxy (6) + ai (6)
│   │   └── _i18n.py               # CLI i18n — .ts reuse, tr() function
│   ├── proxy/                     # Proxy module (7 files)
│   │   ├── _config.py             # ProxyType/ProxyAuth/ProxyEndpointSpec/ProxyProfile/ParsedProxy/ProxyConfig
│   │   ├── _consumers.py          # PROXY_CONSUMERS — static consumer→display-name registry
│   │   ├── _hook.py               # ProxyHook Protocol + LoggingProxyHook (cold-path, sync)
│   │   ├── _probe.py              # ProbeResult + probe_one() — TCP connectivity pre-check
│   │   ├── _proxy.py              # ProxyManager (SWRR) + ProxyHealthTracker (3-tier CB)
│   │   └── _session.py            # ProxySession — composite root + lifecycle facade (DI, async ctx mgr)
│   └── utils/                     # Utilities (6 files)
│       ├── url.py                 # URL normalize + credential redaction
│       ├── html.py                # HTML parse + RobustHash (head-middle-tail sampling)
│       ├── logging.py             # Logfmt config + Qt log bridge
│       ├── preferences.py         # Preferences — 16 data fields, AI/Proxy Profile CRUD, C-mode
│       └── _atomic.py             # POSIX atomic writes (mkstemp → fsync → os.replace)
├── LICENSE                         # Apache 2.0
├── tests/                         # pytest test suite (103 test files, 3,659 tests)
│   ├── conftest.py                # Shared + GUI fixtures
│   ├── _fakes.py / _fakes_gui.py  # Core / GUI test doubles
│   ├── Kernel: test_types test_config test_constants test_version test_resilience test_health test_health_monitor
│   │           test_diagnostics test_startup test_packaged test_json_compat test_main_entry test_retry_strategy test_path_strategy
│   ├── Crawler: test_engine test_outcomes test_liveness test_supervisors test_progress test_signals test_url_gate
│   ├── Browser: test_browser_pool test_browser_navigation test_browser_page_pool test_browser_slot_pool test_browser_context_pool
│   │             test_browser_domain_memory test_proxy_classifier test_device_caps test_preview
│   ├── Network: test_robots test_sitemap test_sitemap_discovery test_throttling test_fetch
│   ├── Storage: test_db test_db_expanded test_writer
│   ├── Rules: test_rules_engine test_rules_lifecycle test_rules_loader test_rules_source test_rules_diagnostics test_rules_state
│   │           test_rules_io test_rules_browser_edge test_rules_markdown test_html_preprocess test_chatml
│   ├── AI: test_ai_client test_ai_errors test_ai_generation test_ai_profile test_ai_provider test_ai_rate_limiter test_ai_rules
│   │        test_ai_template test_ai_constraint + ai_openai/test_client ai_anthropic/test_client ai_google/test_client
│   ├── Proxy: test_proxy test_proxy_config test_proxy_session test_proxy_probe
│   ├── Utils: test_url test_html test_utils_expanded test_atomic test_preferences test_logging
│   ├── CLI: test_cli_main test_cli_rules test_cli_source test_cli_proxy test_cli_ai
│   └── GUI: test_gui_core test_gui_theme test_gui_mainwindow_data test_gui_dialogs test_gui_worker_viz test_gui_mainwindow_behavior
│             test_gui_rules_dialog test_gui_tokens test_gui_style test_gui_delegates test_gui_table_page test_gui_ai_profile
│             test_gui_animated_bar test_preview_session test_preview_panel test_gui_i18n test_gui_proxy_config
│             test_proxy_endpoint_dialog test_proxy_profile_page test_route_settings_page
├── docs/                          # Documentation
│   └── guides/                    # developer-guide.md + gui-standards.md + terminology.md
├── pyproject.toml
└── README.md
```

## Documentation

| Document | Content |
|------|------|
| [`docs/guides/developer-guide.md`](docs/guides/developer-guide.md) | Contributing / Architecture / Development reference |
| [`docs/guides/gui-standards.md`](docs/guides/gui-standards.md) | GUI coding standards (signal naming, theme tokens, Dirty Check) |

## Development

```bash
pip install -e ".[dev]"

pytest                              # All tests (3,659)
pytest --cov=astrocrawl              # With coverage
pytest -m "not gui"                 # Skip GUI tests
mypy astrocrawl/                     # Type check (strict mode)
ruff check astrocrawl/               # Lint (py312, line-length=120)
ruff format astrocrawl/              # Format
```

`from __future__ import annotations` enforced globally (Ruff FA100 + isort check), private functions/classes default to `_` prefix, `if TYPE_CHECKING:` for deferred imports.

## Tech Stack

| Layer | Technology |
|----|------|
| Language | Python 3.12+ |
| Browser Automation | Playwright (Chromium headless) |
| HTTP Client | aiohttp + TCPConnector |
| Database | aiosqlite (WAL mode) |
| HTML Parsing | BeautifulSoup4 + lxml |
| Regex Engine | google-re2 (linear-time, ReDoS-immune, hard runtime dependency) |
| AI Client | openai / anthropic / google-genai (multi-provider, entry point auto-discovery) |
| GUI | PySide6 (Qt6 for Python, Fusion style, QTranslator zh_CN) |
| Config | Pydantic v2 + JSON/YAML/TOML |
| Retry | Built-in ProxyFailureClassifier + aiohttp_retry_fetch |
| Testing | pytest + pytest-asyncio + pytest-cov |

## Security

- **Atomic Writes** — POSIX protocol (mkstemp → write → fsync → os.replace → chmod 0o600), modeled on SQLite WAL / PostgreSQL WAL / Git core.fsync
- **Concurrent Locking** — fcntl.flock guards rules_state.json and sources.json RMW cycles, modeled on git `.git/index.lock`
- **DNS Rebinding Hard Block** — 12 private/reserved IP ranges checked, preventing SSRF + DNS rebinding attacks
- **Unicode Sanitization** — display_name/author/description auto-filter Bidi Override / C0 control chars / Interlinear Annotation, modeled on Unicode TR36 + Git ident validation
- **AI Injection Defense** — Five-layer OWASP LLM01 defense (URL reconstruction, field validation, XML boundaries, output validation, user confirmation flow)
- **Extraction Layer Truncation** — max_text_length moved upstream into `_extract_value` (all three branches), UTF-8 byte-aware truncation, preventing 50MB `<div>` memory amplification
- **Transform Two-Gate** — Absolute ceiling + ratio ceiling, independent log events, each defending distinct attack vectors
- **URL Redaction** — Proxy credentials and 15+ sensitive parameters (token, key, secret, jwt, etc.) automatically hidden in logs; source URLs limited to scheme://netloc
- **Chromium Log Suppression** — `--log-level=3` prevents credential leakage into browser debug output
- **API Key Protection** — AIProfile `__repr__` shows only first 8 characters + "...", API keys never appear in logs
- **File Permissions** — Output reports `chmod 600`, Preferences file `chmod 0o600`
- **Cookie Validation** — Only `.json` extension accepted, array format and entry structure verified
- **Remote Rule Sources** — HTTPS-only, SHA256 manifest verification, on-demand download
- **Rule Regexes** — re2 hard dependency ensures linear-time matching, zero ReDoS attack surface

## License

Apache 2.0 © Etoileint

## Version History

| Version | Description |
|------|------|
| **v0.1.1** | PyPI README update — install guide improvements, dynamic badges |
| **v0.1.0** | Initial public release — modular async crawler, GUI + CLI dual interface |
