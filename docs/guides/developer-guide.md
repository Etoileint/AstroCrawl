# AstroCrawl 开发者指南

> **注意**: 全部统计数据（文件数/行数/测试数/功能点数）运行 `python scripts/generate_tree.py --write` 自动更新 `docs/feature-tree.md`。

> 贡献指南 / 架构设计 / 开发参考

---

## 第一章：贡献指南

### 1.1 环境搭建

```bash
git clone https://github.com/Etoileint/AstroCrawl.git
cd AstroCrawl
pip install -e ".[dev,fast,monitor,yaml]"
playwright install chromium
```

### 1.2 开发依赖

| 组 | 包含 | 用途 |
|------|------|------|
| `dev` | pytest, pytest-asyncio, pytest-cov, mypy, ruff | 测试、类型检查、Lint |
| `fast` | orjson, pydantic | JSON 加速、配置验证 |
| `monitor` | psutil | 资源监控 |
| `yaml` | pyyaml | YAML 配置文件支持 |

### 1.3 运行测试

```bash
pytest                                    # 运行全部测试（运行 `pytest --collect-only -q` 获取当前计数）
pytest -v --tb=short                       # 详细输出
pytest --cov=astrocrawl --cov-report=term-missing  # 覆盖率
pytest -k "test_normalize"                 # 按名称筛选
pytest tests/test_db.py                    # 指定文件
```

异步测试使用 `@pytest.mark.asyncio` 装饰器，`conftest.py` 配置 `asyncio_mode = "auto"`。

### 1.4 测试文件

> 行数为 v0.1.3 快照，仅供参考。

| 文件 | 测试内容 | 行数 |
|------|---------|------|
| `tests/conftest.py` | 共享夹具（test_config / fake_state / fake_writer）+ GUI fixtures | 130 |
| `tests/_fakes.py` | 测试替身（FakeBrowserPool / FakeContextPool / FakeBrowser / FakeBrowserContext / FakeProxyManager / FakePage / FakePagePool / FakeWriter）| 501 |
| `tests/test_config.py` | CrawlerConfig 字段验证、默认值、from_env、ConfigError、GlobalSettings | 569 |
| `tests/test_db.py` | CrawlState 队列操作、去重、重试、深度管理 | 274 |
| `tests/test_html.py` | extract_text_from_soup、extract_links_from_soup、extract_title、compute_robust_hash、check_meta_robots | 517 |
| `tests/test_atomic.py` | POSIX 原子写入原语 atomic_write_json (18 用例) | 283 |
| `tests/test_preferences.py` | Preferences 路径记忆/主题/LLM/AI 速率限制/迁移 (36 用例) | 1440 |
| `tests/test_logging.py` | logfmt 日志配置幂等 + Qt 桥接 (13 用例) | 346 |
| `tests/test_json_compat.py` | JSON 序列化兼容 orjson/stdlib (8 用例) | 124 |
| `tests/test_startup.py` | 启动依赖检测 + Chromium 验证 (25 用例) | 303 |
| `tests/test_packaged.py` | 打包模式检测 (9 用例) | 108 |
| `tests/test_main_entry.py` | main.py CLI/GUI 入口分发 + __main__ PEP 338 守卫 (8 用例) | 302 |
| `tests/test_url.py` | normalize_url、strip_www (PSL)、redact_*、safe_log_url、验证 (8 classes, 45 tests) | 315 |
| `tests/test_writer.py` | AsyncJsonlWriter JSONL+GZip 全生命周期 (39 用例) | 571 |
| `tests/test_outcomes.py` | UrlOutcome 分类、CrawlStats 规则统计/并发安全/发现计数器、FetchAttempt | 411 |
| `tests/test_proxy.py` | ProxyHealthTracker 状态机/查询/延迟评分/_probe_loop + ProxyManager 选择器 (56 用例) | 688 |
| `tests/test_proxy_classifier.py` | ProxyFailureClassifier 错误分类与策略分发 (37 用例, 100% 覆盖) | 250 |
| `tests/test_robots.py` | robots.txt 解析、规则匹配、代理回退、异常分类 | 701 |
| `tests/test_throttling.py` | DomainRateLimiter + DomainConcurrencyLimiter ISTQB 边界值 (18 用例) | 358 |
| `tests/test_sitemap.py` | Sitemap 发现、解析、代理回退 | 558 |
| `tests/test_liveness.py` | LivenessTracker — 心跳/存活/停滞/边界值 (20 用例) | 151 |
| `tests/test_supervisors.py` | Supervisor + WorkerSupervisor — OTP one_for_one/Fuse 边界 (23 用例) | 420 |
| `tests/test_progress.py` | ProgressReporter — CLI/GUI 双模/信号 payload/摘要 (26 用例) | 595 |
| `tests/test_engine.py` | AsyncCrawler — Pipeline/_worker 异常路径/Processor 边界/恢复路径 + _run_worker_loop (77 用例) | 1704 |
| `tests/test_url_gate.py` | UrlGate AdmitResult 全路径 + 深度边界 (8 用例) | 86 |
| `tests/test_browser_navigation.py` | safe_goto 双层超时包装 (6 用例) | 73 |
| `tests/test_browser_page_pool.py` | PagePool 创建/销毁/关闭/重试成功/goto 容错/幂等 (14 用例) | 175 |
| `tests/test_browser_slot_pool.py` | SlotPool 创建/替换/销毁/查询/Cookie (30 用例) | 401 |
| `tests/test_browser_context_pool.py` | ContextPool init/proxy/scoped_path (15 用例) | 418 |
| `tests/test_browser_domain_memory.py` | DomainPathMemory TTL 双缓存 (27 用例) | 222 |
| `tests/test_browser_pool.py` | BrowserPool 消息模式、四种代理模式、重试策略 | 854 |
| `tests/test_fakes.py` | FakeBrowserPool / FakeBrowser / FakeBrowserContext / FakeProxyManager / FakePage / FakePagePool / FakeWriter（22 测试）| 217 |
| `tests/test_resilience.py` | Fuse 两态熔断器全边界 — 状态机/窗口滑动/回调/Health (25 用例) | 298 |
| `tests/test_health.py` | Health 数据类 + aggregate() Semigroup 聚合 (21 用例) | 227 |
| `tests/test_health_monitor.py` | HealthMonitor A/B/C 调度 + 主动被动聚合 + 生命周期 (50 用例) | 872 |
| `tests/test_diagnostics.py` | HTTP /health 端点 + TaskDumper + CrawlDiagnostics (31 用例) | 447 |
| `tests/test_signals.py` | CrawlerSignals + TestSignalPayloads emit→connect→handler 完整链路 (16 用例) | 225 |
| `tests/test_ai_client.py` | AIClient Provider 门面, _ResolvedParams, Hook 链, StreamEvent (69 用例, ADR-0006/0008) | 1350 |
| `tests/test_ai_errors.py` | AIError 层次 9 类 + 重试分类 + 错误实例化 (34 用例) | 214 |
| `tests/test_ai_constraint.py` | OutputConstraint 结构化输出 + 能力降级 + Provider 能力 (49 用例, ADR-0008) | 489 |
| `tests/test_ai_provider.py` | _ChatProvider / _SupportsEmbedding Protocol + entry point 发现 (27 用例) | 276 |
| `tests/test_ai_rate_limiter.py` | TokenBucket + BoundedSemaphore + UsageTracker (35 用例, 100% cov) | 497 |
| `tests/ai_openai/test_client.py` | _map_error + OpenAIClient 单元测试 | 300 |
| `tests/test_ai_generation.py` | RuleGenerator AI 规则生成端到端 (6 用例) | 79 |
| `tests/test_ai_rules.py` | AI 生成规则验证与导入 (27 用例) | 749 |
| `tests/test_ai_template.py` | AI Prompt 模板加载/回退 + Schema 契约测试 (19 用例) | 228 |
| `tests/test_ai_profile.py` | AIProfile 多 Profile 管理 (25 用例, 100% cov) | 243 |
| `tests/test_rules_state.py` | fcntl 锁状态机 + 损坏恢复 + 优雅降级 (23 用例) | 415 |
| `tests/test_rules_engine.py` | 规则引擎核心 — 加载/匹配/提取/Transform | 1817 |
| `tests/test_rules_lifecycle.py` | 规则生命周期 — 启用/禁用/删除/校验 | 613 |
| `tests/test_rules_source.py` | 远程规则源 — Manifest/下载/增量更新 | 1533 |
| `tests/test_rules_diagnostics.py` | 规则诊断 — trace 模式/统计 | 169 |
| `tests/test_html_preprocess.py` | HTML 三级清洗预处理 (19 用例) | 150 |
| `tests/test_chatml.py` | ChatML 序列化/tiktoken (15 用例) | 95 |
| `tests/test_cli_rules.py` | CLI rules 子命令 (argparse) | 708 |
| `tests/test_cli_source.py` | CLI source 子命令 (argparse) | 216 |
| `tests/_fakes_gui.py` | GUI 测试替身（FakePreferences / FakeCrawlSession / FakeRuleLifecycle）| 546 |
| `tests/test_gui_core.py` | Phase 1: CrawlSession 状态机 + CrawlerThread (49 用例) | 795 |
| `tests/test_gui_theme.py` | Phase 2: ThemeManager + ThemeDialog + _SwatchField (59 用例) | 610 |
| `tests/test_gui_mainwindow_data.py` | Phase 3: MainWindow get_urls/_validate/slot/config (41 用例) | 408 |
| `tests/test_gui_dialogs.py` | Phase 4: AdvancedSettingsDialog + GlobalSettings + AI 设置 (36 用例) | 578 |
| `tests/test_gui_worker_viz.py` | Phase 5: WorkerStatusBar + ProxyHealthBar + TitleBar (20 用例) | 328 |
| `tests/test_gui_mainwindow_behavior.py` | Phase 6: MainWindow _run_crawler/closeEvent/_cleanup_session/_reset_app (47 用例) | 911 |
| `tests/test_gui_rules_dialog.py` | Phase 7: RulesDialog + _RuleTablePage/_CustomPage/_SourcePage (84 用例) | 1095 |
| `tests/test_gui_ai_profile.py` | ADR-0007: _AIProfilePage + AIProfileEditDialog (37 用例) | 704 |
| `tests/test_gui_delegates.py` | ADR-0007: StatusColorDelegate + CheckboxDelegate (13 用例) | 230 |
| `tests/test_gui_table_page.py` | ADR-0007: _TableManagementPage + _FilterProxy (12 用例) | 255 |
| `tests/test_gui_tokens.py` | 布局 Token 常量验证 (11 用例) | 107 |
| `tests/test_gui_style.py` | ColumnDef + create_managed_table + style helpers (25 用例) | 317 |

### 1.5 GUI 测试运行

```bash
QT_QPA_PLATFORM=offscreen pytest tests/test_gui_*.py -m gui
```

所有 GUI 测试标记 `@pytest.mark.gui`，使用 `offscreen` QPA 无头运行 (无需 Xvfb)。
关键适配：`_SignalCollector` 替代 PySide6 中不支持下标的 `QSignalSpy`，`QMessageBox` 全局 patch 防模态对话框阻塞。

### 1.6 已知问题

| Bug | 位置 | 状态 |
|-----|------|------|
| `disconnect_signals()` 遗漏 `worker_state.disconnect()` | `crawl_session.py` | 已修复 (b457e86) |
| ~~`rule_match`/`rule_stats` 信号源缺失~~ | `signals.py:_RealWorkerSignals` | 已废弃 — 规则匹配通过 RuleSnapshot 同步执行 |

### 1.7 代码质量

```bash
mypy astrocrawl/              # 类型检查
ruff check astrocrawl/        # Lint
```

### 1.8 提交流程

1. 确保所有测试通过（`pytest`）
2. 类型检查通过（`mypy astrocrawl/`）
3. Lint 通过（`ruff check astrocrawl/`）
4. i18n 检查通过 — 新增 UI 字符串必须包裹 `self.tr()`（pre-commit hook 自动拦截）
5. 提交信息遵循项目惯例：`type: 简短描述`（如 `refactor:`、`fix:`、`feat:`、`test:`、`docs:`）

### 1.9 多语支持（i18n）

AstroCrawl **GUI + CLI 统一**通过 Qt QTranslator 体系（GUI）+ `.ts` 直解析（CLI）实现 en↔zh_CN 双语。英文为源语言，中文为翻译目标。**GUI 与 CLI 共享同一 `.ts` 翻译源（SSOT）**。

**架构概览**：

```
GUI: self.tr("Start Crawl")          ← QObject 子类，进入命名上下文（类名）
CLI: tr("Crawl depth")               ← 模块级纯函数，进入无名上下文 <name />
     → .ts 文件: <source>Start Crawl</source>
                 <translation>开始爬取</translation>
     → .qm 编译 (lrelease6)           ← GUI 用 .qm，CLI 直接解析 .ts
     → 运行时: "开始爬取"
```

**添加新 UI 字符串的规范**：

1. 始终使用英文源文本
2. **GUI**：`self.tr("Ready")`（QObject 子类内）
3. **CLI**：`tr("Crawl depth")`（从 `astrocrawl.cli._i18n` 导入）
4. 格式化表头必须用 `.format()` 而非 f-string 内嵌 `tr()`：
   ```python
   # ✗ lupdate6 看不到 f-string {} 内的 tr()
   print(f"{tr('Name'):30s}")
   # ✓ lupdate6 能看到 .format() 参数中的 tr()
   print("{Name:30s}".format(Name=tr("Name")))
   ```
   **原因**：`lupdate6`（Qt 6.11）不解析 f-string 表达式内的函数调用。
5. pre-commit hook（`check_i18n.py`）会阻止裸中文 UI 字符串提交
6. 术语映射查 `docs/guides/terminology.md` SSOT

**翻译更新流程**（必须同时扫描 gui/ + cli/）：

```bash
lupdate6 -extensions py astrocrawl/gui/ astrocrawl/cli/ -ts astrocrawl/gui/translations/astrocrawl_gui_zh_CN.ts
# 编辑 .ts 填写新翻译（lupdate6 自动跨上下文合并已有翻译）
python scripts/check_ts_dicts.py --fix-vanished astrocrawl/gui/translations/astrocrawl_gui_zh_CN.ts
lrelease6 astrocrawl/gui/translations/astrocrawl_gui_zh_CN.ts -qm astrocrawl/gui/translations/astrocrawl_gui_zh_CN.qm
```

> **关键约束**：`lupdate6` **必须同时扫描 `gui/` 和 `cli/`** 两个目录。只扫描一个会导致另一目录的全部源字符串被标记为 `type="vanished"`，编译出的 `.qm` 丢失全部翻译（2026-06-30 教训）。

**语言切换**：高级设置 → 全局设置 → 外观 group → 重启生效（写入 `preferences.language`，GUI + CLI 同步切换）。

**关键文件**：

| 文件 | 用途 |
|------|------|
| `astrocrawl/gui/_i18n.py` | GUI QTranslator 生命周期管理 |
| `astrocrawl/cli/_i18n.py` | CLI `tr()` 函数 + `.ts` XML 直解析 |
| `astrocrawl/gui/translations/astrocrawl_gui_zh_CN.ts` | 翻译源 SSOT（XML，871 条，GUI+CLI 共享） |
| `astrocrawl/gui/translations/astrocrawl_gui_zh_CN.qm` | 编译后的运行时翻译（仅 GUI 使用） |
| `scripts/check_i18n.py` | pre-commit hook — 阻止裸中文提交（扫描 gui/ + cli/） |
| `scripts/check_ts_dicts.py` | pre-commit hook — 字典常量翻译覆盖 |
| `scripts/check_qm_fresh.py` | pre-commit hook — .qm 新鲜度检查 |
| `docs/guides/terminology.md` | 术语映射 SSOT（三档：全翻/半翻/保留） |

**常见陷阱**：

| 陷阱 | 后果 | 预防 |
|------|------|------|
| `lupdate6` 只扫一个目录 | 另一目录全部翻译 vanished | 始终同时指定 `gui/ cli/` |
| f-string 内嵌 `tr()` | `lupdate6` 看不到，翻译缺失 | 用 `.format(kw=tr(...))` |
| 翻译术语偏离 SSOT | 用户体验不一致 | 每次翻译前查 `terminology.md` |
| `lupdate6` 未加 `-extensions py` | `Found 0 source text(s)` | 始终加 `-extensions py` |
| lupdate6 后未编辑 .ts | 新增字符串空翻译 | 检查 `<translation type="unfinished">` 并填写中文 |

---

## 第二章：架构设计

### 2.1 分层架构

```
┌─────────────────────────────────────────────────────────┐
│                    入口层 Entry                          │
│  main.py  →  CLI (cli/main.py)  /  GUI (gui/*.py)       │
├─────────────────────────────────────────────────────────┤
│                    引擎层 Engine                         │
│  crawler/engine.py     — AsyncCrawler + Pipeline (8 处理器)│
│  crawler/outcomes.py   — 结果分类与统计                   │
│  crawler/progress.py   — ProgressReporter 进度发射        │
│  crawler/supervisors.py— WorkerSupervisor 监督器          │
│  crawler/liveness.py   — LivenessTracker 存活追踪         │
│  crawler/signals.py    — CrawlerSignals Qt 信号封装       │
├─────────────────────────────────────────────────────────┤
│                    规则层 Rules                           │
│  rules/   — RuleSnapshot → match → CSS extract → transform│
│  ai/      — AIClient (领域无关 AI 基础设施底座)           │
├─────────────────────────────────────────────────────────┤
│                    基础设施层 Infrastructure              │
│  browser/   — BrowserPool + ContextPool + SlotPool + PagePool│
│  network/   — aiohttp_retry_fetch + RobotsCache + RateLimiter×2 + SitemapDiscovery│
│  proxy/     — ProxyManager (SWRR) + ProxyHealthTracker (3-tier CB)│
│  storage/   — CrawlState (SQLite) + AsyncJsonlWriter    │
│  gui/theme.py — ThemeManager (15 令牌 + QPalette 引擎)   │
├─────────────────────────────────────────────────────────┤
│                    工具层 Utilities                      │
│  utils/url.py          — URL 规范化/脱敏/验证            │
│  utils/html.py         — HTML 解析/内容哈希              │
│  utils/logging.py      — 日志系统                        │
│  utils/preferences.py  — 用户偏好（路径记忆 + 主题配置）  │
├─────────────────────────────────────────────────────────┤
│                    配置与常量                             │
│  config.py       — CrawlerConfig 冻结数据类              │
│  _constants.py   — 命名常量                              │
│  _types.py       — 共享内核（枚举、错误分类、协议）       │
│  _json_compat.py — JSON 序列化兼容                       │
│  _startup.py     — 启动依赖检测                           │
│  _version.py     — 版本号                                │
│  _packaged.py    — 打包模式检测                           │
│  health.py       — Health 数据类 + HealthChecked 协议    │
│  resilience.py   — Fuse 两态熔断器                        │
│  diagnostics.py  — 三层运行时诊断                         │
└─────────────────────────────────────────────────────────┘
```

核心原则：**引擎层不依赖任何 CLI 或 GUI 层代码**。`AsyncCrawler` 通过可选的 `CrawlerSignals` Qt 信号对象与 GUI 通信，CLI 模式下该参数为 no-op 存根。

### 2.2 设计哲学

| 原则 | 说明 | 体现 |
|------|------|------|
| **正确性优先** | 宁慢勿漏，宁停勿错 | in_flight 崩溃恢复、原子队列操作、双重空队列确认 |
| **优雅降级** | 单点故障不导致全局失败 | 槽位隔离、代理自动轮换、robots.txt 获取失败时允许所有 |
| **状态可恢复** | 任何时刻中断后均可从断点继续 | SQLite 全量持久化、in_flight 恢复、boundary_links 暂存 |
| **资源可控** | 明确的上限和清理策略 | 队列硬上限、域名状态定期清理、内容哈希定期清理 |
| **安全内建** | 敏感信息保护是默认行为 | URL 参数脱敏、代理凭据脱敏、Chromium 日志级别限制 |
| **接口隔离** | 模块依赖窄接口（ISP，PEP 544 Protocol）| CrawlStateProtocol（storage/_protocol.py）|
| **可测试性** | Fake 注入替代完整 DI 框架 | `_fakes.py` 测试替身（FakeBrowserPool、FakeCrawlState 等）|

**为什么选择 Playwright 而非纯 HTTP**：大量现代网站依赖 JavaScript 渲染核心内容（SPA、动态加载）。Playwright 提供完整的浏览器环境（DOM、JS 执行、网络拦截）。资源过滤（`route.abort()`）屏蔽图片/字体/媒体，将额外开销降至最低。代价：每个浏览器上下文约 150-300MB 内存，通过限制并发数和上下文池化管理。

### 2.3 模块依赖与 ISP 接口隔离

**依赖关系**：

```
_types.py  ←  （模块级零内部导入，共享内核；default_only() 延迟导入破循环依赖）
     ↓
config.py  ←  _constants.py, _version.py
     ↓
crawler/engine.py  ←  config.py, _constants.py, _types.py
     ↓                browser/*, network/*, storage/*, rules/*
rules/*    ←  ai/* (RuleGenerator), _types.py
ai/*       ←  （领域无关，零内部导入）
cli/main.py  ←  crawler/engine.py, config.py, rules/*
gui/*.py     ←  crawler/engine.py, config.py, crawler/signals.py, rules/*
```

**ISP 窄接口（PEP 544 Protocol）**：

为解决 `CrawlerConfig`（50 字段）被多模块消费、`CrawlState`（~730 行）被 engine 依赖的问题，引入多个 Protocol 接口：

- **`CrawlStateProtocol`**（`storage/_protocol.py`，88 行）—— 完整接口，约 30 个方法签名。另提供 `CrawlStateReader`、`CrawlStateWriter`、`CrawlStateAdmin` 三个更窄的视角。
- **`AsyncCloseable`**（`_types.py`）—— 异步资源生命周期协议：`async def aclose()`。
- **`HealthChecked`**（`health.py`）—— 统一健康报告协议：`get_health() → Health`。

**为什么用 PEP 544 Protocol 而非 ABC**：
1. ABC 要求显式继承，侵入现有代码
2. Protocol 是结构子类型——有方法就自动满足，无需改任何现有代码
3. FakeBrowserPool / FakeCrawlState 在测试文件中实现相同方法签名即可自动满足 Protocol
4. mypy/Pyright 完整支持

**测试 seam**：`_fakes.py` 提供完整的 Fake 测试替身（FakeBrowserPool、FakeContextPool、FakeCrawlState、FakeWriter），测试通过依赖注入使用替身，跳过 Playwright 启动和真实浏览器资源。

### 2.4 URL 处理流水线

**规范化**（`normalize_url`）：原始 URL → 小写化 scheme 和 host → 剥离默认端口（`:80`/`:443`）→ IDN 编码（非 ASCII 域名 → Punycode）→ 移除追踪参数（10 个默认，`tracking_params` 可配置）→ 剥离 fragment 和 `;params` → 规范化路径（去尾 `/`）→ 规范化 URL。内部使用 `p.hostname` + `p.port`（标准库已解析字段），避免手动字符串拆分。

`tracking_params` 存储为 `frozenset`（不可变 + O(1) 成员检测），用 `p.lower()` 预处理确保大小写不敏感。

**入队前过滤**（`UrlGate.admit()`）：候选 URL → URL 格式校验 → 排除模式正则匹配 → 深度检查（超限→存入 boundary_links）→ `push_to_queue_single()` 事务内双重查重（`urls` 表 AND `queue` 表）+ 队列容量检查 → INSERT OR IGNORE → 更新内存队列计数。使用 `BEGIN IMMEDIATE` 事务杜绝 TOCTOU 竞态条件。所有入队路径（种子/Sitemap/链接/Heal）统一通过此门禁。

### 2.5 并发与速率控制

**双层控制架构（机制/策略分离）**：

```
┌──────────────────────────────────┐
│  DomainTracker                   │  ← 机制层: 统一域名状态容器
│  (_DomainState: next_allowed +    │     _sem + active_count + last_used)
│  rate_lock + custom_delay +       │     单一字典, 单一锁, 单一清理
└──────────┬───────────────────────┘
           │
┌──────────▼───────────────────────┐
│  DomainConcurrencyLimiter        │  ← 策略层: 同域名同时请求数上限
│  (asyncio.Semaphore)             │     try_acquire 复合原子操作
└──────────┬───────────────────────┘
           │
┌──────────▼───────────────────────┐
│  DomainRateLimiter               │  ← 策略层: 同域名请求最小间隔
│  (rate_lock + time.monotonic)    │     锁仅保护时间戳比较 (微秒), sleep 在锁外
└──────────────────────────────────┘
```

**DomainRateLimiter**：获取 tracker 状态 → 计算 interval（优先 state.custom_delay，否则 random(min, max)）→ 获取 `state.rate_lock`（微秒级持有）→ 更新 `next_allowed = now + interval` → 计算 `delay = next_allowed - now` → 释放 rate_lock → 若 delay > 0：`await asyncio.wait_for(stop_event.wait(), timeout=delay)`（支持停止信号中断）。

**为什么 sleep 在锁外**：多个 worker 可同时为同一域名独立计算等待时间并休眠——实际吞吐量由 `DomainConcurrencyLimiter` 决定，而非被 lock 串行为 1。锁仅保护 `next_allowed` 时间戳的原子比较与更新。

**Crawl-Delay 优先级**：robots.txt 声明的 Crawl-Delay 覆盖随机延迟范围。`Request-Rate`（`请求数/秒数`）自动转换为等价延迟（`秒数/请求数`）。

### 2.6 崩溃恢复机制

**核心问题**：异步爬虫随时可能中断（Ctrl+C、进程崩溃、断电）。如果队列状态仅在内存中，中断后所有未处理 URL 永久丢失。

**方案**：in_flight 表 + 原子出队。

```
正常流程:
  pop_from_queue():
    BEGIN IMMEDIATE
    SELECT url, depth FROM queue LIMIT 1     # 拾取
    DELETE FROM queue WHERE id=?             # 从队列删除
    INSERT INTO in_flight(url, depth, ...)   # 标记"处理中"
    COMMIT

  mark_completed(url):
    UPDATE urls SET status='completed', outcome=?
    DELETE FROM in_flight WHERE url=?

崩溃恢复:
  _recover_in_flight():
    SELECT url, depth FROM in_flight         # 上次未完成的
    INSERT OR IGNORE INTO queue(...)         # 重新入队
    DELETE FROM in_flight                    # 清理
```

如果进程在 `pop_from_queue()` 后、`mark_completed()` 前崩溃，`in_flight` 表中的记录在下一次启动时被 `_recover_in_flight()` 重新入队。

**双重空队列确认**：队列为空 1s 后再次确认 → 仍为空且 in_flight 为 0 → 才判定爬取完成。这是为了给正在入队新链接的 worker 时间完成操作，以及给 worker 完成当前 URL 处理后提取新链接。

**深度变化处理**：
- 运行时：`UrlGate.admit()` 在入队时检查 `depth >= max_depth`，超限 URL 自动存入 `boundary_links`
- 深度减小：`purge_queue_depth_ge()` 将超深 URL 移入 `boundary_links`，同时清理 failures 表
- 深度增大：`promote_boundary_links()` 自动将暂存的子链接展开入队
- 自愈检测：`saved_plan > saved_proc` 时从 `boundary_links` 找回丢失子链接

**进度恢复公式**：
```
proc = max(saved_proc, db_done)
plan = max(saved_plan, proc + db_queued)
```
- `saved_plan` 保留原始入队总数作为上限参考——差异在进度条上可见
- 当 `saved_plan > proc + db_queued` 时说明有 URL 流失，触发自愈机制

### 2.7 内容去重算法

**稳健哈希**（`compute_robust_hash`）：短文本（≤ sample_size=4096）→ 全文归一化空白 → MD5。长文本 → 头/中/尾各 1/3 采样 → 拼接 → 归一化空白 → MD5。

**为什么头/中/尾采样**：仅头或仅尾采样对页面类型有偏——头部有导航和页眉（模板化），尾部有页脚（模板化），中间是主体内容。三者结合减少模板噪声对哈希的影响。

**为什么用 MD5 而非 SHA256**：内容去重不需要密码学安全性。MD5 128 位输出对去重足够（碰撞仅导致漏爬一个页面，不是安全问题）。采样大幅减少 CPU 和内存开销。

**过期清理**：每 30 分钟清理超过 24h 的哈希条目。平衡"跨多次爬取的重复防护"和"数据库大小控制"。

### 2.8 robots.txt 解析

**RFC 9309 规则优先级**：最具体的匹配规则优先（the most specific match found MUST be used）。specificity = 原始规则路径的字符数——路径越长 → 匹配越精确 → 越具体。

这并非"Allow 优先"的朴素实现——例如 `Disallow: /foo/bar`（specificity 8）比 `Allow: /foo`（specificity 4）更具体，即使 Allow 在文件中出现更晚。

**规则编译**：`*` → `.*`；`$` 行尾锚点保留；无 `$` 规则自动追加 `.*`（前缀匹配）。使用 `re.fullmatch()` 确保完整路径匹配。

**路径解码**：URL 路径在匹配前先做百分号解码（RFC 9309 §2.2.2），`%2F` 还原为 `/`、`%20` 还原为空格，通过 `urllib.parse.unquote()` 实现。

**缓存与容错**：以源站为键缓存解析结果，TTL 3600s，最大 1000 条目（LRU 逐出）。获取失败时 `is_allowed()` 返回 `True`（fail-open），遵循 RFC 9309。

### 2.9 浏览器上下文管理

**槽位模型**：`ContextPool` 管理 `concurrency` 个槽位，每个槽位包含 1 个 BrowserContext + 1 个 PagePool + 1 个代理。页面用完即销毁，不复用。可用槽位通过 `asyncio.Queue` 管理。

**为什么用槽位而非池**：代理绑定（每槽位固定代理，用完归还）、故障隔离（单槽位崩溃不影响其他）、故障恢复（`replace_context()` 精确替换）、Cookie/认证隔离（每上下文独立）。

**上下文替换策略**：先建新再毁旧——先尝试创建新上下文（最多 3 次重试），成功后先置空槽位指针（避免竞态窗口），再关闭旧页面池和旧上下文，将新资源写入槽位。失败则标记槽位失效，所有槽位失效时抛出 RuntimeError。

**PagePool 设计**：页面用完即销毁，不复用。每次 `acquire()` 通过 `context.new_page()` 创建新页面（最多 3 次重试），调用方负责关闭。`remove_broken()` 先导航到 about:blank 清理待处理操作，再安全关闭。页面级回收的清理成本（unroute + 4 类存储清除 + about:blank）超过新建成本（单次 CDP Target.createTarget），因此不做页面池化，复用由 BrowserContext 槽位层完成。

**Playwright TargetClosedError 抑制**：关闭浏览器上下文时，Playwright 内部的后台超时等待器尝试与已关闭的 target 通信并抛出 `TargetClosedError`。安装自定义 asyncio 异常处理器静默抑制此异常，其他异常正常处理。

### 2.10 重试系统

**双层重试架构**：

```
┌────────────────────────────────────┐
│  即时重试 (BrowserPool._handle 内部) │  ← 同 URL 同上下文
│  依据 ProxyFailureClassifier.classify()  │     ROTATE_PROXY 不计入 max_retries
│  TRANSIENT/REPLACE_CONTEXT 消耗     │     代理池自然耗尽为终止
│  最多 max_retries 次（默认 3）      │
└──────────────┬─────────────────────┘
               │ 重试用尽
┌──────────────▼─────────────────────┐
│  延迟重试 (CrawlState.try_schedule_ │  ← 不同 worker，可能不同上下文
│              retry 周期扫描)         │     BEGIN IMMEDIATE 事务原子回收
│  每 10s 扫描 failures 表            │     批量 50 个/次
└────────────────────────────────────┘
```

**错误分类与策略**（`ProxyFailureClassifier.classify()` 统一入口，`_types.py` 的 `classify_fetch_error()` 为错误字符串→类别映射 SSOT）：

| 触发条件 | 策略 | 退避 | 消耗 retries |
|----------|------|------|:---:|
| PROXY 类别 / 代理超时 / 代理路径 DNS 或连接被拒 | ROTATE_PROXY — 轮换代理 | 0（立即轮换） | 否 |
| 直连超时 / Target 或 Session 关闭 | REPLACE_CONTEXT — 替换浏览器上下文 | 0（立即替换） | 是 |
| CONNECTION_RESET / ABORTED / HTTP_5XX / 429 / GENERIC / 未映射类别 | TRANSIENT — 同代理/同上下文退避重试 | Full Jitter (`uniform(0, base^attempt)`) | 是 |
| SSL / DNS(直连) / HTTP_4XX(非429) / 下载 / 重定向过多 / 连接被拒(直连) | FATAL — 直接放弃 | — | N/A |

> **注**：PROXY_EXHAUSTED 和 CONTEXT_FAILURE 在 classify() 层默认 TRANSIENT，实际由 BrowserPool Phase 2 回退层和 `is_infra` 裁决层分别处理。429 始终经 `http_status` 路径 → `_classify_http(429)` → TRANSIENT，不走 `HTTP_4XX → FATAL` 静态映射。

**为什么 Target closed 不退避**：问题在于本地浏览器资源，替换上下文后通常立即可用，等待退避无意义。

**atomic_retry_reclaim 的竞态安全**：`try_schedule_retry()` 和 N 个 `_worker` 可能同时尝试重试同一个 URL。使用 `BEGIN IMMEDIATE` + 单事务内 `SELECT → INSERT queue → UPDATE retry_count` 保证串行化。

### 2.11 线程安全与异步模型

AstroCrawl 采用**单线程异步模型**：所有 I/O 操作通过 asyncio 协程执行，SQLite 使用 aiosqlite（异步驱动）。

**CrawlStats 线程安全**：`asyncio.Lock` 保护所有写操作（`record_outcome()` 等）。用 asyncio.Lock 而非 threading.Lock——asyncio.Lock 不阻塞事件循环（协程等待锁时让出控制权）。

**GUI 线程模型**：

```
主线程 (Qt GUI)                 工作线程 (asyncio event loop)
┌──────────────────┐           ┌──────────────────────┐
│ MainWindow       │  ←信号──  │ CrawlerThread         │
│ QWidget          │           │  └─ AsyncCrawler.run()│
│ _add_log()       │           │     └─ _worker()      │
│ _update_stats()  │           │     └─ _progress_...() │
│ _on_finished()   │  ──槽→    │                        │
└──────────────────┘           └──────────────────────┘
```

信号方向：Worker → GUI（单向）。控制方向：GUI → Worker 通过 CrawlerThread 的方法（`stop()`、`pause()`、`resume()`）。日志桥接：Python logging Handler 将日志记录通过信号发送到 GUI 主线程。

### 2.12 安全设计原理

**URL 敏感信息脱敏**（`safe_log_url`）：基于参数名的正则替换精确脱敏 22 种敏感查询参数（password、token、api_key、secret、sessionid、jwt、bearer 等）。代理凭据脱敏（`://user:***@host`）。空值敏感参数同样脱敏。

**Chromium 日志抑制**：启动参数 `--log-level=3` 将 Chromium 日志限制为仅 ERROR，防止代理凭据在调试输出中泄漏。

**输出文件权限**：`os.chmod(report_path, 0o600)` 确保最小权限。爬取结果可能包含敏感业务数据。

**Cookie 文件校验**：仅接受 `.json` 后缀，验证数组格式和条目结构（缺失 name/value 记录警告）。

### 2.13 关键设计决策汇总

**SQLite 而非 Redis/PostgreSQL**：零依赖、单文件便于备份迁移、WAL 模式 + aiosqlite 支持高并发读写。AstroCrawl 的写入模式是天然的"多 reader + 单 writer 队列"，SQLite 的 WAL 模式完全胜任。

**冻结数据类（frozen=True）而非可变配置**：配置实例可在多个协程间安全共享（不可变 = 线程安全）。修改时 `replace()` 创建新实例，原实例不受影响。避免"配置在爬取过程中被意外修改"的 bug。

**边界链接持久化（boundary_links）+ 自愈**：超出当前深度的子链接通过 `UrlGate.admit()` 自动存入 `boundary_links` 表（深度内链接直接入队，不冗余存储）。恢复时若检测到缺口，从 `boundary_links` 找回丢失子链接并入队——零额外 HTTP 请求。

**robots.txt data/policy 分离**：`RobotsCache` 始终创建并获取 robots.txt（data 层），Sitemap 发现和 Crawl-Delay 独立可用。`_robots_processor` 仅在 `robots_respect=True` 时执行 Disallow 拦截（policy 层）。获取失败时允许所有（fail-open），与 RFC 9309 一致。

**资源拦截而非 DomContentLoaded 后取消**：`page.route("**/*", filter)` 在请求发出前就拦截图片/字体/媒体，阻止网络请求。CSS 和 JS 不禁用——对页面渲染和内容完整性必要。

**去重与内容保存分离**：内容重复页面仍正常提取链接（除非 `skip_duplicate_links=True`），以免遗漏发现机会。

**`flush()` 显式持久化点**：Python `sqlite3` 文档明确 `close()` 不回滚也不隐式提交未完成事务，未提交写入静默丢失。`CrawlState.flush()` 在关闭前显式 COMMIT，语义化为"确保所有待处理写入持久化到存储"。非事务型后端（如 Postgres autocommit）可将 `flush()` 实现为空操作。

### 2.14 数据流全景

```
                        启动
                         │
                ┌────────▼────────┐
                │   加载/恢复配置   │
                └────────┬────────┘
                         │
                ┌────────▼────────┐
                │  打开 SQLite DB  │
                │  recover in_flight│
                │  深度清理/提升   │
                └────────┬────────┘
                         │
            ┌────────────┼────────────┐
            │            │            │
   ┌────────▼───┐ ┌─────▼──────┐ ┌───▼──────────┐
   │ Sitemap    │ │ Robots     │ │ 种子 URL 入队 │
   │ 发现       │ │ Cache 初始化│ │              │
   └────────┬───┘ └─────┬──────┘ └───┬──────────┘
            │            │            │
            └────────────┼────────────┘
                         │
                ┌────────▼────────┐
                │  启动 Worker × N │
                │  后台任务 × 8   │
                └────────┬────────┘
                         │
            ┌────────────▼────────────┐
            │    主循环监控            │
            │  - max_total_pages      │
            │  - max_runtime_seconds  │
            │  - queue + in_flight    │
            └────────────┬───────────┘
                         │
                ┌────────▼────────┐
                │  停止 Worker     │
                │  关闭资源        │
                │  生成报告        │
                │  Webhook 通知    │
                └─────────────────┘
```

---

## 第三章：开发参考

### 3.1 目录结构详解

```
astrocrawl/
├── __init__.py               ← 包入口：导出 CrawlerConfig, AsyncCrawler
├── __main__.py               ← python -m astrocrawl 入口
├── main.py (107 行)          ← 启动入口：判断 CLI vs GUI + 主题初始化
├── _version.py (6 行)        ← 版本号 "0.1.0" (PEP 440) + __version_info__ (PEP 396)，pyproject.toml 通过 attr: 动态读取
├── _constants.py (376 行)    ← 全部命名常量（已按子系统分组注释）
├── _types.py (255 行)        ← 共享内核：枚举、错误分类（15 种）、PathSwitch、AsyncCloseable、FetchErrorCategory SSOT
├── _retry_strategy.py (64 行)← 重试策略分类（classify_http/classify_from_category）
├── _path_strategy.py (148 行)← PathSwitch — 4 模式代理路由（for_mode/should_fallback）
├── _json_compat.py (26 行)   ← JSON 序列化兼容（orjson 回退 stdlib）
├── _startup.py (99 行)       ← 启动时依赖检测 + Chromium 浏览器验证
├── _packaged.py (54 行)      ← 打包模式检测与浏览器路径适配
├── config.py (348 行)        ← CrawlerConfig 冻结数据类（50 字段）+ ConfigError + GlobalSettings
├── health.py (58 行)         ← Health 数据类 + HealthChecked Protocol + health_to_report()
├── resilience.py (107 行)    ← Fuse 两态熔断器（CLOSED → OPEN）
├── diagnostics.py (256 行)   ← 三层运行时诊断（SIGUSR1 / HTTP /health / 自动 dump）
├── health_monitor.py (175 行) ← HealthMonitor — 统一健康检查调度（A/B/C 分类 + 被动指示器）
│
├── cli/                      ← 命令行接口
│   └── main.py (1771 行)     ← argparse 定义 + 配置合并 + rules/source 子命令
│
├── crawler/                  ← 爬虫引擎核心
│   ├── engine.py (2148 行)   ← AsyncCrawler + Pipeline (8 Processors) + PipelineDeps
│   ├── outcomes.py (381 行)  ← UrlOutcome / FetchErrorCategory / CrawlStats
│   ├── progress.py (204 行)  ← ProgressReporter — CLI/GUI 进度发射与摘要
│   ├── supervisors.py (112 行) ← WorkerSupervisor — one_for_one 监督器
│   ├── liveness.py (53 行)   ← LivenessTracker — 心跳存活检测
│   ├── signals.py (89 行)    ← CrawlerSignals — Qt 信号封装（含 CLI no-op + worker_state）
│   └── _url_gate.py (82 行)   ← UrlGate — 统一 URL 准入门禁（AdmitResult 6 态，纯策略 @staticmethod）
│
├── browser/                  ← 浏览器管理
│   ├── browser_pool.py (848 行) ← BrowserPool — Actor 消息模式，三阶段抓取，内部重试
│   ├── context_pool.py (179 行) ← ContextPool — 槽位生命周期管理 + ConfigError 启动门控
│   ├── _slot_pool.py (279 行)   ← SlotPool — 槽位分配、代理绑定、路径切换
│   ├── page_pool.py (79 行)     ← PagePool — 页面生命周期管理
│   ├── _retry.py (41 行)        ← ProxyFailureClassifier + RetryStrategy SSOT
│   ├── _domain_memory.py (54 行)← DomainPathMemory — 双缓存域名路径记忆（TTL 过期）
│   ├── _device_caps.py (23 行)  ← 设备 GPU 能力检测，SwiftShader fallback
│   ├── _preview.py (299 行)     ← PreviewBrowser — 纯 async headed Chromium 预览
│   └── navigation.py (38 行)    ← safe_goto — 带超时保护的页面导航
│
├── network/                  ← 网络层
│   ├── _fetch.py (152 行)       ← aiohttp_retry_fetch — 与 BrowserPool 策略等价的 aiohttp 重试引擎
│   ├── robots.py (319 行)       ← AsyncRobotsParser + RobotsCache（RFC 9309）
│   ├── sitemap.py (554 行)      ← SitemapParser + SitemapDiscovery
│   └── throttling.py (206 行)   ← DomainRateLimiter + DomainConcurrencyLimiter
│
├── rules/                    ← 提取规则引擎
│   ├── _schema.py               ← RuleSchema + FieldRule + MatchConfig 数据模型
│   ├── _loader.py               ← build_rule_snapshot() 规则快照构建
│   ├── _matcher.py              ← match_url() by_domain 索引驱动匹配 + LRU 域名缓存
│   ├── _extractor.py            ← extract_fields() CSS 选择器提取 + fallback
│   ├── _transform.py            ← apply_transforms() 五种变换流水线
│   ├── _html_preprocess.py      ← preprocess_html() 三级 HTML 清洗预处理
│   ├── _template.py             ← get_prompt_template() + _generate_schema_example() AI Prompt 模板
│   ├── _chatml.py               ← serialize_chatml() ChatML 序列化 + tiktoken Token 统计
│   ├── _ai.py                   ← RuleGenerator + _assemble_messages() AI 辅助规则生成
│   ├── _source.py               ← SourceManager 远程源生命周期管理
│   ├── _io.py                   ← rule_to_dict() 规则序列化
│   ├── _lifecycle.py            ← 规则启用/禁用/删除生命周期
│   ├── _state.py                ← 规则状态持久化
│   ├── _markdown.py             ← clean_markdown_wrapper() AI 输出清洗
│   └── __init__.py              ← 公共 API 导出
│
├── ai/                        ← ADR-0006 多 Provider AI 底座（领域无关）
│   ├── _client.py               ← AIClient 统一门面（Provider dispatch）
│   ├── _config.py               ← AIConfig + GenerationParams + _ResolvedParams
│   ├── _types.py                ← ChatMessage / ChatResponse / StreamEvent(5) / ToolCall / EmbedResult
│   ├── _errors.py               ← AIError 层次 (9 类, Provider 无关)
│   ├── _provider.py             ← _ChatProvider + _SupportsEmbedding Protocol
│   ├── _provider_registry.py    ← importlib.metadata entry point 发现
│   ├── _rate_limiter.py         ← TokenBucket + BoundedSemaphore 线程安全限流
│   ├── _usage_tracker.py        ← 会话级 TokenUsage 累计
│   ├── _observability.py        ← AIHook + LoggingHook (OTel 对齐)
│   ├── _profile.py              ← AIProfile (10 字段, 含 provider + api_key masked __repr__)
│   ├── providers/               ← 内置 Provider 实现（3 文件）
│   │   ├── openai.py             ← OpenAIClient (pip install astrocrawl[openai])
│   │   ├── anthropic.py          ← AnthropicClient (pip install astrocrawl[anthropic])
│   │   └── google.py             ← GoogleClient (pip install astrocrawl[google])
│   └── __init__.py              ← 公共 API 导出
│
├── proxy/                     ← ADR-0010 代理模块
│   ├── _config.py (292 行)       ← ProxyType/ProxyAuth/ProxyEndpointSpec/ProxyProfile/ProxyConfig
│   ├── _proxy.py (451 行)        ← ProxyManager (SWRR) + ProxyHealthTracker (3-tier CB)
│   ├── _session.py (232 行)      ← ProxySession — 组合根 + 生命周期门面 (DI, async ctx mgr)
│   ├── _probe.py (46 行)         ← ProbeResult + probe_one() — TCP 连通性预检
│   ├── _hook.py (44 行)          ← ProxyHook Protocol + LoggingProxyHook
│   └── _consumers.py (15 行)     ← PROXY_CONSUMERS — 静态 consumer→display-name 注册表
│
├── storage/                  ← 持久化层
│   ├── _protocol.py (89 行)     ← CrawlStateProtocol — PEP 544 窄接口（full/reader/writer/admin）
│   ├── db.py (728 行)           ← CrawlState — SQLite 队列/去重/状态/恢复
│   └── writer.py (117 行)       ← AsyncJsonlWriter — 缓冲 JSONL 写入器（含 GZip）
│
├── gui/                      ← 图形界面（24 文件，~9,200 行）
│   ├── main_window.py (967 行)    ← MainWindow — Qt 主窗口
│   ├── advanced_dialog.py (611 行)← AdvancedSettingsDialog — 高级设置（5 Tab：常规/全局/AI/代理/路由）
│   ├── rules_dialog.py (2798 行)  ← RulesDialog — 规则管理（3 Tab）+ RuleEditDialog + _SourceEditDialog + Workers
│   ├── completion_dialog.py (194 行)← CompletionReportDialog — 完成报告弹窗
│   ├── crawl_session.py (211 行)  ← CrawlSession QObject — 爬取生命周期状态机
│   ├── thread.py (80 行)          ← CrawlerThread — QThread 异步事件循环
│   ├── proxy_health_bar.py (159 行)← ProxyHealthBar — 代理健康可视化
│   ├── theme.py (176 行)          ← ThemeManager — 主题管理器 + 15 令牌预设
│   ├── theme_dialog.py (279 行)   ← ThemeDialog — 主题设置（模式外置 + 色块 QScrollArea）
│   ├── title_bar.py (61 行)       ← TitleBar — 标题栏（状态条 + 主题按钮）
│   ├── worker_status_bar.py (76 行)← WorkerStatusBar — Worker 脉动渐变状态条
│   ├── _tokens.py (40 行)         ← 布局常量 — 间距/圆角/字体/高度/动画
│   ├── _style.py (105 行)         ← ColumnDef + create_managed_table + style helpers
│   ├── _delegates.py (132 行)     ← StatusColorDelegate (token 化) + CheckboxDelegate (editorEvent + 居中)
│   ├── _table_page.py (228 行)    ← _TableManagementPage + _FilterProxy (Template Method)
│   ├── _ai_profile_page.py (750 行)← _AIProfilePage + AIProfileEditDialog (ADR-0007)
│   ├── _proxy_profile_page.py     ← _ProxyProfilePage + ProxyProfileEditDialog
│   ├── _proxy_endpoint_dialog.py  ← ProxyEndpointEditDialog — 7 字段端点编辑器
│   ├── _route_settings_page.py    ← _RouteSettingsPage — consumer→profile 路由
│   ├── _preview_session.py        ← PreviewSession — MVP Presenter
│   ├── _preview_panel.py          ← PreviewPanel — 规则可视化预览非模态 Singleton
│   ├── _i18n.py                   ← GUI i18n — QTranslator 生命周期 (en↔zh_CN)
│   └── _animated_bar.py           ← QTimer 驱动的动画条基类
│
└── utils/                    ← 工具函数
    ├── url.py (118 行)            ← URL 规范化、脱敏、PSL 域名提取、验证
    ├── html.py (227 行)          ← HTML 解析、链接提取、内容哈希
    ├── logging.py (87 行)        ← 日志设置 + Qt 日志桥接 + logfmt 格式
    ├── _atomic.py (61 行)        ← atomic_write_json POSIX 原子写入原语
    └── preferences.py (579 行)   ← Preferences — 用户偏好持久化（路径记忆 + 主题 + LLM 配置）
```

### 3.2 核心模块详解

#### 配置系统（`config.py`）

`CrawlerConfig` 是 `@dataclass(frozen=True)` 冻结数据类，50 字段。冻结意味着配置实例不可变——任何修改通过 `dataclasses.replace()` 创建新实例。

**验证路径**：`__post_init__` 执行字段验证（值范围、非空等）。配置为冻结数据类（`frozen=True`），修改通过 `dataclasses.replace()` 创建新实例。

**配置加载链**：`CrawlerConfig.from_file(path)` → `CrawlerConfig.from_env(base)` → `replace(cfg, **overrides)` → `cfg.with_contact(contact)`。

**关键设计**：`tracking_params` 存储为 `frozenset`（不可变 + O(1) 查找）；`user_agent` 在有关联 `contact_info` 时自动追加联系方式。跨会话全局设置由 `GlobalSettings` frozen dataclass 管理（7 字段，含 `log_level`/`output_gzip`/`trace_rules` 等），通过 `Preferences` 持久化并显式注入引擎。

#### 爬虫引擎（`crawler/engine.py`）

`AsyncCrawler` 是核心类，管理完整的爬取生命周期。

**初始化**：规范化起始 URL → 解析 allowed_domains → 创建 AsyncJsonlWriter → 初始化进度层 → 预编译 exclude_patterns 正则 → 设置异步事件。

**运行流程**：打开 SQLite → 恢复/续爬/全新初始化 → 创建 HTTP 会话 → 初始化 RobotsCache（无条件，data 层）→ Sitemap 发现 → 启动超时预算稽核日志（一次性）→ 启动 HealthMonitor（6 项检查）+ ProgressReporter + Sitemap 发现 + 代理探测等后台任务 → 启动 Playwright → 创建 ContextPool → 启动 worker → 主循环等待完成条件 → 清理与报告。

**Worker 主循环**：`while not stop_event: _pop_domain_aware() → _process_url() → _settle_url()`。`_pop_domain_aware()` 实现 URL Frontier 两级调度器（Layer 1 每域名独立 FIFO + Layer 2 跨域轮询跳过饱和域名）。proxy_only 模式下全部代理 OPEN 时暂停出队，等待 `proxy_recovery_event` 后自动恢复。`_settle_url()` 统一处理统计记录、in_flight 清理和进度更新。`_process_url()` 返回 `(UrlOutcome, UrlDisposition)` 元组——`UrlDisposition` 枚举（COMPLETED / REQUEUED / FAILED）控制 in_flight 清理路径。

**`_fetch_url` 契约**：Result 类型模式——始终返回 `FetchAttempt`，捕获 `TimeoutError` 和 `CancelledError` 转为 `FetchAttempt` 返回，不向上抛异常。

**单 URL 处理**（Processor Chain，8 个 Processor）：domain_concurrency 获取 → robots 检查 → rate limiter 获取 → BrowserPool 抓取（含 per-fetch 和 fallback 两级 asyncio 超时安全网）→ HTML 解析 → 内容去重 + JSONL 写入 → 结果确定 + mark_done → 链接入队/边界暂存。每个 Processor 签名统一：`async (ctx, deps) → ctx`，通过 `ctx.is_terminal` 控制短路。

**后台任务**（直接 `asyncio.create_task` + HealthMonitor 定时检查双轨制）：

直接创建的后台协程（`_background_tasks` 列表管理）：
- `WorkerSupervisor.supervise()` — Worker 存活监督，one_for_one 替换
- `_background_source_update()` — 后台更新远程规则源（有远程源时）
- `_sitemap_wrapper()` — Sitemap 自动发现（`use_sitemap=True` 时）
- `ProgressReporter.run()` — 进度报告（1s GUI / 5s CLI）
- `BrowserPool._actor_task` — Actor 消息循环
- `BrowserPool._health_task` — 浏览器 CDP 健康检查

HealthMonitor 定时检查（`HealthCheckSpec` 注册，6 项）：

| 规格 | 间隔 | 分类 | 功能 |
|------|------|------|------|
| `retry_monitor` | 10s | ALERT | 批量原子重试失败 URL（CrawlState.try_schedule_retry）|
| `rate_limiter_cleanup` | 600s | ALERT | 清理过期域名速率锁 |
| `concurrency_limiter_cleanup` | 300s | ALERT | 清理过期域名并发信号量 |
| `content_hash_cleanup` | 1800s | REPORT | 清理过期内容哈希（24h TTL）|
| `resource_monitor` | 60s | REPORT | 报告内存/DB/队列状态 |
| `rules_engine` | 300s | ALERT | 规则引擎加载状态检查 |

#### BrowserPool（`browser/browser_pool.py`）

核心页面抓取器，采用 Actor 消息模式。Worker 通过 `await pool.send(FetchRequest(...))` 获取 `FetchResponse | FetchError`，不直接接触 Playwright 对象。

**三阶段抓取**（`_do_fetch`）：Phase 0 域名记忆快捷路径（命中则跳过主路径）→ Phase 1 主路径重试循环（`_retry_loop`）→ Phase 2 路径回退（`scoped_path` 切换到备选路径，成功写入域名记忆）。`is_infra` 仅在最终裁决——`CONTEXT_FAILURE` 为 True。

**重试策略**：ROTATE_PROXY（不消耗 `retries_remaining`，以 `rotate_proxy()→False` 自然终止）/ REPLACE_CONTEXT / TRANSIENT / FATAL。TRANSIENT 和 REPLACE_CONTEXT 消耗 `max_retries`（默认 3）。

**proxy_only 暂停**：`should_pause_dequeuing()` 检测全部代理 health_score==0.0 时返回 True，Engine 通过 `proxy_recovery_event` 等待代理恢复后自动恢复出队。独立后台协程定期 CDP ping 检测浏览器存活，失败自动重启。

#### 结果分类系统（`crawler/outcomes.py`）

**UrlOutcome 枚举**（9 种）：`OK`、`TRUNCATED`、`DUPLICATE`、`NOINDEX`、`PARSE_FAILED`、`ROBOTS_DENIED`、`FETCH_ERROR`、`INTERNAL_ERROR`、`STOPPED`。两个分类维度：`is_success`（URL 被正确处理）和 `is_failure`（技术故障）。

**FetchErrorCategory 子分类**（15 种）：`dns`、`ssl`、`timeout`、`http_4xx`、`http_5xx`、`proxy`、`proxy_exhausted`、`context_failure`、`connection_refused`、`connection_reset`、`target_closed`、`aborted`、`download`、`too_many_redirects`、`generic`。通过 `ERROR_PATTERNS` 字典 + `classify_fetch_error()` SSOT 分类。

**DropReason 枚举**（9 种）：`exclude_pattern`、`nofollow_link`、`cross_domain`、`invalid_url`、`queue_full`、`already_visited`、`skip_duplicate_links`、`same_page_dup`、`download_candidate`。

#### 浏览器管理层（`browser/`）

**BrowserPool**（`browser_pool.py`）：Actor 消息模式主控。Worker 通过 `send(FetchRequest)` 获取 `FetchResponse | FetchError`。内部管理完整重试循环（4 种策略）+ 健康检查 + 生命周期管理。对标 Erlang gen_server 消息模式。

**ContextPool**（`context_pool.py`）：槽位生命周期管理——创建/替换/获取/释放 `concurrency` 个槽位。

**SlotPool**（`_slot_pool.py`）：机制层——槽位分配、代理绑定、路径切换、健康跟踪。机制（SlotPool）与策略（ContextPool）分离。

**PagePool**（`page_pool.py`）：每上下文页面生命周期管理——创建（带重试）、销毁坏页面、关闭全部。页面用完即销毁不复用，槽位级复用已捕获全部有意义资源复用。

**ProxyManager + ProxyHealthTracker**（`_proxy.py`）：三级断路器（CLOSED → OPEN → HALF_OPEN → CLOSED）+ health_score 四因子评分（base − 故障密度 − 连续延迟惩罚 0→0.15 + 成功奖励）+ TCP 握手延迟 EWMA（α=0.3）+ 后台探测自动恢复 + 锁无关快照模式（`_probe_ok` 私有计数器真单写者）+ `all_proxies_dead()` 全 OPEN 检测。每个代理独立状态机。

**页面导航**（`navigation.py`）：`safe_goto()`——带超时缓冲的 Playwright 导航包装器。

**DomainPathMemory**（`_domain_memory.py`）：双缓存域名路径记忆——`remember()`（缓存域名需代理）+ `remember_direct()`（缓存域名需直连），分别服务 prefer_direct 和 prefer_proxy 的 Phase 0 快捷路径。TTL 过期自动清理。

#### 网络层（`network/`）

**RobotsCache**（`robots.py`）：RFC 9309 完整实现——规则编译、最具体匹配优先、通配符/锚点支持、Crawl-Delay/Request-Rate 解析、Sitemap 提取。LRU 缓存（TTL 3600s，最大 1000 条目），获取失败时 fail-open。

**SitemapDiscovery**（`sitemap.py`）：递归发现——robots.txt → 默认路径 → Sitemap Index 递归（最多 2 层，每层最多 10 万 URL）。遇到新域名自动触发发现。

**速率限制**（`throttling.py`）：机制/策略分离 — DomainTracker 统一域名状态（单字典/单锁/两阶段清理）→ DomainConcurrencyLimiter（信号量并发控制）+ DomainRateLimiter（时间戳预约限速，停止信号可中断）。优先使用 robots.txt 声明的 Crawl-Delay。

#### 提取规则引擎（`rules/`）

规则引擎实现声明式结构化内容提取，流水线为：加载 → 匹配 → CSS 提取 → Transform → 输出。

**RuleSchema**（`_schema.py`）：规则数据模型——`RuleSchema`（顶层：name/schema_version/version/display_name/description/author/tags/enabled/test_urls + match + fields + options）、`FieldRule`（字段定义，含 selector/description/extract/attr/multiple/fallback/transform）、`MatchConfig`（匹配条件：domains + url_pattern）、`RuleOptions`（行为开关：keep_body_text/follow_links）。`validate_rule()` 执行完整 JSON Schema 校验 + re2 正则编译预检（url_pattern 和 transform.regex）。

**build_rule_snapshot()**（`_loader.py`）：三层规则源合并——pip 预置规则 → 远程规则源 → 用户自定义规则（`~/.astrocrawl/rules/`）。优先级：用户 > 远程 > 预置 > default。所有规则原子加载到一个不可变 RuleSnapshot，包含已启用规则列表 + 域名索引。热重载时原子替换引用。

**match_url()**（`_matcher.py`）：by_domain 索引驱动匹配——RuleMatchCache LRU 缓存命中 → by_domain O(1) 精确域名哈希 → 逐级父域后缀匹配 → _generic_rules 泛型扫描。候选排序：域名精确度（精确=0 > 后缀=1 > 泛型=2）> url_pattern 长度 > version 数字。源优先级在加载阶段通过 `_deduplicate_rules()` 消歧，不参与匹配排序。总复杂度 O(域名深度 + 泛型数) ≈ O(25)。

**extract_fields()**（`_extractor.py`）：CSS 选择器提取核心。支持三种伪类（`text`/`attr:name`/`html`）、`multiple: true` 返回数组、fallback 链（最多 3 层）、简写/完整两种字段写法。整规则一次 `to_thread` 避免逐字段线程切换。字段级异常隔离——单字段失败不影响其他字段。

**apply_transforms()**（`_transform.py`）：五种变换——`strip`（去空白）、`strip_currency`（去货币符号）、`regex`（re2 正则提取，线性时间防 ReDoS）、`replace`（字符串替换）、`join`（数组拼接）。

**RuleGenerator**（`_ai.py`）：AI 辅助规则生成——消费 `ai.AIClient`，通过零样本 Prompt 模板（Schema 即文档）将 HTML + 用户指令转换为规则 JSON。自动清洗 markdown 包装 + 自动验证选择器。

**SourceManager**（`_source.py`）：远程规则源生命周期——sources.json 管理源列表 → HTTPS Manifest 下载（SHA256 校验）→ 规则文件按需下载 → 增量更新（manifest hash 比对，仅内容变化时重新下载规则文件）。`validate_source_url()` 强制 HTTPS-only。

**规则生命周期**（`_lifecycle.py` + `_state.py`）：启用/禁用/删除操作，状态持久化到 `~/.astrocrawl/rules_state.json`。`"default"` 规则不可禁用、不可删除。

**安全设计**：HTTPS-only 远程源、SHA256 manifest 校验、re2 正则（ReDoS 免疫）、POSIX 原子写入（mkstemp → fsync → os.replace → chmod 0o600）、fcntl.flock 并发锁、DNS 重绑定硬阻断（12 IP 段）、Unicode 控制字符清洗（TR36）、AI 提示注入五层防御（OWASP LLM01）、提取层字节感知截断、Transform 两道门放大防护、文件大小上限（单文件 64KB，manifest 1MB）。

> 详细设计见 ADR-0005 和 `docs/reports/architecture/architecture-audit-extraction-rules-2026-05-27.md`。

#### AI 基础设施（`ai/`）— ADR-0006

领域无关的多 Provider 通用 AI 底座。两层架构：`AIClient` 门面 + Provider entry point 自动发现。三 Provider 已内置，按需安装 SDK（`astrocrawl[openai]` 等），本地模型通过 `provider="openai"` + `base_url` 接入。核心零 Provider SDK import。

**AIClient**（`_client.py`）：统一门面。`chat(messages, tools=None, params=None)` / `achat()` 单轮对话，`chat_stream()` / `achat_stream()` 返回 5 事件 `StreamEvent`，`embed(texts)` Embeddings。硬编码调用链：`RateLimiter → Provider Call → UsageTracker → Observability`。支持 `async with` 上下文管理器。

**AIConfig + GenerationParams**（`_config.py`）：`AIConfig` 含 `api_key`/`provider`/`base_url`/`default_model`/`default_temperature`/`default_max_tokens`/`timeout`/`max_retries`（8 字段）。`GenerationParams` 全字段 `Optional[None]`，`None` 从 `AIConfig` 填充。`_ResolvedParams` + `_resolve_params()` 内部消歧（`is not None` 检查，`temperature=0.0` 不误判）。

**Provider 接口**（`_provider.py`）：`_ChatProvider` Protocol（5 方法：chat/chat_stream/achat/achat_stream/aclose，sync+async 双套）。`_SupportsEmbedding` Protocol（1 方法：embed，async-only）。ISP 分离——不设统一 BaseProvider。

**Provider 注册**（`_provider_registry.py`）：`importlib.metadata.entry_points(group="astrocrawl.ai.providers")` 自动发现。`create_provider(config, **kwargs) -> _ChatProvider` 工厂模式。未安装 Provider → `AIProviderUnavailableError`（含安装指引）。

**RateLimiter**（`_rate_limiter.py`）：线程安全 Token Bucket（自实现，~30 行）+ `threading.BoundedSemaphore`。sync `chat()` 和 async `achat()` 共享同一预算（async 通过 `asyncio.to_thread()` 委托）。`RateLimitConfig(rpm=0, concurrency=0)` 完全禁用。

**UsageTracker**（`_usage_tracker.py`）：会话级 `TokenUsage` 累计。`client.usage` 属性返回累计值。不做费用计算。

**类型系统**（`_types.py`）：`Role`（SYSTEM/USER/ASSISTANT/TOOL）、`ChatMessage`（+tool_call_id/+name）、`ToolCall`（arguments 已解析 dict）、`ChatResponse`（+tool_calls/-raw）、5 事件 `StreamEvent` discriminated union（StreamText/StreamToolCallStart/StreamToolCallDelta/StreamToolCall/StreamFinish）、`EmbedResult`。

**错误层次**（`_errors.py`）：`AIError` 基类 + 8 子类（含 `AIProviderUnavailableError`）。各 Provider 包内部实现 SDK 异常映射——核心不 import 任何 SDK。

**可观测性**（`_observability.py`）：`AIHook` Protocol（on_request/on_response/on_error/on_retry）。`LoggingHook` 默认实现，event 命名对齐 OTel GenAI 语义约定（`gen_ai.{operation}.request/response/error/retry_exhausted`）。`on_retry` 语义修正：SDK retry 耗尽后触发，仅可重试错误。

**AIProfile**（`_profile.py`）：10 字段 frozen dataclass（name/provider/model/api_key/endpoint/temperature/max_tokens/enabled/last_test_status/last_test_time）。`__repr__` 自动掩码 api_key 仅显示前 8 字符。

> 详细设计见 ADR-0006: `docs/adr/0006-ai-module-multi-provider-architecture.md`

**当前能力**：Chat Completion（sync `chat()` + async `achat()`）+ Streaming（sync `chat_stream()` + async `achat_stream()`）+ Embeddings（`embed()`）+ Tool Calling。Multi-turn Conversation 由调用方管理消息列表。

> CLI/GUI 层均已更新为支持 rules 和 source 命令，CLI 通过 `cli/main.py` 子命令分发，GUI 通过 `rules_dialog.py`（规则管理 + AI 生成）和 `AdvancedSettingsDialog` AI Tab 提供 AI配置界面。

#### 持久化层（`storage/`）

**CrawlState**（`db.py`）：SQLite 状态管理，aiosqlite 异步驱动，WAL 模式。

| 表名 | 用途 | 关键字段 |
|------|------|---------|
| `urls` | 统一 URL 记录 | url (PK), depth, status, added_time, outcome |
| `visited_urls` | 已访问 URL 索引 | url (PK), visited_at |
| `completed_urls` | 已完成 URL 记录 | url (PK), completed_at, outcome |
| `content_hashes` | 内容哈希去重 | hash (PK), url, created_at |
| `queue` | 待爬队列 | id (PK AUTOINCREMENT), url (UNIQUE), depth, added_time |
| `failures` | 失败记录 | url (PK), depth, error, timestamp, retry_count |
| `in_flight` | 进行中记录 | url (PK), depth, started_at |
| `meta` | 元数据 | key (PK), value |
| `boundary_links` | 深度边界暂存 | parent_url, child_url, parent_depth (联合 PK) |

关键设计：原子队列操作（单事务 SELECT → DELETE → INSERT in_flight）、批量入队（自动分批避免 999 变量限制）、重试跟踪、原子重试回收（防竞态）、崩溃恢复、旧表迁移、深度管理。

**AsyncJsonlWriter**（`writer.py`）：带缓冲的异步 JSONL 写入，缓冲区和定时双重刷新，可选 GZip 压缩。

#### 工具层（`utils/`）

- **`url.py`**：`normalize_url()`（7 项规范化：小写化/默认端口/IDN/追踪参数/fragment+params/尾斜杠）、`strip_www()`（PSL 感知 www 剥离）、`parse_domain()`（域名提取含 PSL）、`is_valid_http_url()`（RFC 3986 验证）、`safe_log_url()`（20 种敏感参数脱敏 + 代理凭据脱敏）
- **`html.py`**：8 个原子函数 — `check_meta_robots()` / `_remove_content_free_tags()` / `remove_non_content_tags()` / `extract_title()` / `extract_text_from_soup()` / `extract_links_from_soup()` / `extract_schema_org()` / `compute_robust_hash()` — 标签拆分为 `_CONTENT_FREE_TAGS`(5 纯噪声) 与 `_SEMANTIC_CONTAINER_TAGS`(4 语义容器)，链接提取祖先过滤替代标签删除
- **`logging.py`**：`setup_root_logger()`（stdout + RotatingFileHandler）、`attach_qt_handler()` / `detach_qt_handler()`（Qt 信号桥接）
- **`preferences.py`**：`Preferences` — 用户偏好持久化（路径记忆 MRU + 主题配置），POSIX 原子写入（atomic_write_json），自动迁移旧 `path_memory.json`

#### CLI 层（`cli/main.py`）

argparse 参数解析 → 配置文件 → 环境变量 → CLI 参数（优先级递增）合并 → 信号处理器注册 → 爬取运行 → 摘要打印。

#### GUI 层（`gui/`）

- **MainWindow**（967 行）：PySide6 QWidget 主窗口，动态深度层进度条、实时 URL 校验、爬取控制
- **CrawlSession**（211 行）：QObject 状态机，管理爬取生命周期（idle → discovering → running → draining → finished/error），发出 Qt 信号驱动 UI 状态转换
- **CompletionReportDialog**（194 行）：爬取完成弹窗，QTableWidget 展示完整统计明细
- **AdvancedSettingsDialog**（611 行）：CrawlerConfig 全字段编辑表单，5 个标签页（常规设置/全局设置/AI 设置/代理设置/路由设置），常规设置含 6 个 QGroupBox 分区。表单类页面统一使用 QScrollArea 外包，表格类页面（AI/代理/路由设置）使用专用 Page 组件管理。支持"应用"即时预览、"确认"保存关闭、"取消"还原
- **CrawlerThread**（80 行）：QThread 子类，独立线程运行 asyncio 事件循环
- **ProxyHealthBar**（159 行）：代理健康状态可视化，实时显示断路器状态和健康评分。颜色从主题令牌 `danger`/`warning`/`success` 动态读取，深浅主题自动跟随。构造函数接受 theme 参数（ThemeManager），若未传入则自动获取全局单例

#### 主题系统（`gui/theme.py` + `gui/theme_dialog.py`）

**ThemeManager**（`gui/theme.py`，176 行）：GUI 主题管理器单例。基于 15 个设计令牌 + 浅色/深色两套预设（Catppuccin 色板参考）+ 自定义覆盖。`_apply_palette()` 将令牌映射到 QPalette 全部 20 个 ColorRole 自动传播。非标准控件通过 `theme_changed` Signal 动态重绘。模块级 `init_theme_manager(app, prefs)` 初始化 + `get_theme_manager()` 访问。

**ThemeDialog**（`gui/theme_dialog.py`，279 行）：三模式 radio 选择（浅色/深色/自定义）固定在顶部 + 15 令牌色块 QFormLayout 置于 QScrollArea 内。自定义模式下启用色块，点击弹出 `QColorDialog` 取色器。"应用"即时预览，"确认"持久化到 Preferences，"取消"还原。窗口高度走 §4 的 85% 兜底模式。

#### 标题栏与状态条（`gui/title_bar.py` + `gui/worker_status_bar.py`）

**TitleBar**（`gui/title_bar.py`，61 行）：WorkerStatusBar（左，stretch）+ 主题按钮（右，24×24）。主题按钮显示当前模式符号（☀/★/✿），点击弹出 ThemeDialog。

**WorkerStatusBar**（`gui/worker_status_bar.py`，76 行）：24px 高自定义 QWidget。通过 `worker_state(int, str)` 信号推模式跟踪 Worker 状态，无需轮询。`paintEvent` 用 QLinearGradient 4-stop 双周期绘制脉动渐变条——4×width 画布上无缝滚动，速度随活跃 Worker 数量变化。空闲时显示静态 disabled 色。渐变颜色从主题令牌动态读取。

#### 用户偏好（`utils/preferences.py`）

**Preferences**（579 行）：进程级单例 `get_preferences()`。存储于 `~/.astrocrawl/preferences.json`。合并了原 `path_memory.py` 的路径记忆功能 + 主题配置 + AI Profile CRUD + Proxy Profile CRUD + AI 速率限制 + C-mode + 全局设置。POSIX 原子写入（atomic_write_json），损坏/超大文件自动丢弃。首次加载时自动迁移旧 `path_memory.json` 和旧 AI 配置。

### 3.3 常量参考

> `_constants.py`（376 行）按子系统分组组织。以下为关键常量摘要。

#### Worker 与队列

| 常量 | 值 | 说明 |
|------|-----|------|
| `WORKER_IDLE_SLEEP` | 0.5s | 主循环空闲休眠 |
| `RUN_EMPTY_QUEUE_WAIT` | 1s | 队列为空首次等待 |
| `RUN_EMPTY_QUEUE_CONFIRM` | 5s | 队列为空二次确认等待 |

#### 关闭与生命周期

| 常量 | 值 | 说明 |
|------|-----|------|
| `SHUTDOWN_PENDING_TIMEOUT` | 15s | event loop 清理 pending tasks 超时 |
| `SHUTDOWN_EXECUTOR_TIMEOUT` | 10s | executor shutdown 超时 |
| `SHUTDOWN_ASYNCGEN_TIMEOUT` | 10s | async generator shutdown 超时 |
| `IN_FLIGHT_DRAIN_TIMEOUT` | 30s | 停止后等待 in_flight 自然排空超时 |

#### Sitemap 与 robots.txt

> 注: 重试次数与退避基数已统一使用 `CrawlerConfig.max_retries` / `retry_backoff_base`，不再由常量控制。

| 常量 | 值 | 说明 |
|------|-----|------|
| `SITEMAP_FETCH_TIMEOUT` | 8s | Sitemap 单次抓取超时 |
| `SITEMAP_MAX_CONTENT_SIZE` | 50 MiB | Sitemap 单次抓取上限 |
| `SITEMAP_MAX_DECOMPRESSED` | 100 MiB | Sitemap 解压后上限 |
| `ROBOTS_FETCH_TIMEOUT` | 5s | robots.txt 抓取超时 |
| `ROBOTS_FETCH_MAX_CONCURRENT` | 8 | robots.txt 并发抓取上限 |
| `ROBOTS_MAX_SIZE` | 500 KiB | RFC 9309 robots.txt 大小上限 |

#### 浏览器上下文与页面管理

| 常量 | 值 | 说明 |
|------|-----|------|
| `PAGE_CREATE_RETRIES` | 3 | 页面创建最大重试 |
| `PAGE_CREATE_BACKOFF` | 1.0s | 页面创建重试退避基数 |
| `PAGE_CREATE_TIMEOUT` | 15s | context.new_page() asyncio 超时 |
| `PAGE_CLOSE_TIMEOUT` | 5s | page.close() / context.close() 超时 |
| `SLOT_CREATE_RETRIES` | 3 | 槽位创建最大重试 |
| `SLOT_CREATE_BACKOFF` | 1.0s | 槽位创建重试退避基数 |
| `CONTEXT_CREATE_TIMEOUT` | 30s | browser.new_context() asyncio 超时 |
| `BROWSER_LAUNCH_TIMEOUT` | 60s | Chromium 启动超时 |
| `ABOUT_BLANK_ASYNCIO_TIMEOUT` | 5s | remove_broken 中 about:blank goto 超时 |
| `RELEASE_UNROUTE_TIMEOUT` | 3s | 错误恢复时 page.unroute_all() 超时 |

#### 纵深防御超时

| 常量 | 值 | 说明 |
|------|-----|------|
| `CONTENT_READ_TIMEOUT` | 15s | page.content() asyncio 超时 |
| `PROCESS_URL_TIMEOUT` | 240s | _process_url 整体超时（含代理回退余量）|
| `FETCH_PROCESSOR_OVERHEAD` | 15s | 非 fetch processor 时间预算 + 安全余量 |
| `BROWSER_GOTO_BUFFER_S` | 5.0s | asyncio.wait_for 超时超过 Playwright 超时的缓冲 |
| `DOMAIN_CONCURRENCY_ACQUIRE_TIMEOUT` | 60s | 域并发 semaphore 获取超时 |

> **`fetch_budget` 动态计算**（替代固定 `PER_FETCH_TIMEOUT`）：启动时计算 `page_timeout/1000 + BROWSER_GOTO_BUFFER_S + backoff 总和`，超过 `PROCESS_URL_TIMEOUT - FETCH_PROCESSOR_OVERHEAD` 时截断为 `max_budget`。`_fetch_url` 通过 `asyncio.wait_for(..., timeout=fetch_budget)` 传播 deadline。

#### 卡死检测与上下文健康

| 常量 | 值 | 说明 |
|------|-----|------|
| `WORKER_STUCK_TIMEOUT` | 300s | 所有 worker 进度停滞阈值 |
| `STUCK_IN_FLIGHT_TIMEOUT` | 300s | queue=0 但 in_flight>0 悬挂阈值 |
| `CONTEXT_HEALTH_CHECK_INTERVAL` | 30s | 空闲 slot 健康检查间隔 |
| `HTTP_READ_LINE_TIMEOUT` | 10s | HTTP /health 端点 readline() 超时 |
| `BROWSER_RESTART_TIMEOUT_MULT` | 3 | Browser 重启超时 = HARD_CLEANUP_TIMEOUT × 倍数 |

#### 代理健康管理

| 常量 | 值 | 说明 |
|------|-----|------|
| `PROXY_FAILURE_THRESHOLD` | 3 | 连续失败 → OPEN（完全熔断） |
| `PROXY_COOLDOWN` | 30s | OPEN → HALF_OPEN 冷却 |
| `PROXY_COOLDOWN_MAX` | 120s | 冷却 ×1.5 增长上限 |
| `PROXY_HALF_OPEN_MIN_DURATION` | 15s | HALF_OPEN 最短考察窗口 |
| `PROXY_HALF_OPEN_MAX_FAILURES` | 2 | 窗口内失败 ≥ 此值 → OPEN |
| `PROXY_DECAY_SECONDS` | 120s | 故障记录过期时间 |
| `PROXY_PROBE_INTERVAL` | 5s | 后台 TCP 探测间隔 |
| `PROXY_PROBE_TIMEOUT` | 2s | 单次 TCP 连接超时 |
| `PROXY_SCORE_WINDOW` | 15s | health_score 故障密度窗口 |
| `PROXY_SCORE_SUCCESS_DECAY` | 30s | 成功奖励线性衰减 |
| `PROXY_HEALTH_BAR_REFRESH` | 3s | GUI 健康条刷新间隔 |

#### 域管理

| 常量 | 值 | 说明 |
|------|-----|------|
| `DOMAIN_CLEANUP_AGE` | 3600s | 域限流条目闲置后清理阈值 |
| `DNS_CACHE_TTL` | 300s | aiohttp DNS 缓存 TTL |

#### 后台维护周期

| 常量 | 值 | 说明 |
|------|-----|------|
| `RETRY_MONITOR_INTERVAL` | 10s | 失败重试监视器轮询间隔 |
| `RETRY_MONITOR_BATCH` | 50 | 每批原子重试回收最大数量 |
| `RATE_LIMITER_CLEANUP_INTERVAL` | 600s | 域速率限制器过期清理间隔 |
| `CONCURRENT_LIMITER_CLEANUP_INTERVAL` | 300s | 域并发限制器过期清理间隔 |
| `RESOURCE_MONITOR_INTERVAL` | 60s | 资源监控间隔 |
| `HASH_CLEANUP_INTERVAL` | 1800s | 内容哈希表过期清理间隔 |
| `HASH_MAX_AGE` | 86400s (24h) | 内容哈希条目最大保留时间 |

#### 连接器

| 常量 | 值 | 说明 |
|------|-----|------|
| `CONNECTOR_LIMIT` | 100 | aiohttp 总连接数上限 |
| `CONNECTOR_LIMIT_PER_HOST` | 10 | aiohttp 单主机连接数上限 |

#### 日志

| 常量 | 值 | 说明 |
|------|-----|------|
| `FILE_LOG_MAX_BYTES` | 10 MB | RotatingFileHandler 单文件上限 |
| `FILE_LOG_BACKUP_COUNT` | 3 | 历史备份文件数 |
| `MAX_LOG_ITEMS` | 500 | GUI 日志列表最大条目数 |
| `MAX_ERROR_MESSAGE_LENGTH` | 500 | 错误消息截断长度 |

#### Chromium 启动参数

```python
CHROMIUM_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",  # 隐藏自动化标识
    "--disable-dev-shm-usage",                        # 共享内存不足环境兼容
    "--no-sandbox",                                   # 简化部署
    "--disable-gpu",                                  # 无头模式不需要 GPU
    "--log-level=3",                                  # 仅 ERROR，防代理凭据泄漏
]
BLOCKED_RESOURCE_TYPES = frozenset({"image", "font", "media", "websocket", "prefetch", "manifest"})
```

#### GUI 视觉常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `CORNER_RADIUS` | 4 | 所有自定义控件统一圆角 px（WorkerStatusBar / ProxyHealthBar / Swatch 按钮） |
| `QSPINBOX_MAX` | 2147483647 | QSpinBox int32 上界，非业务上限 |
| `QDOUBLESPINBOX_MAX` | 1.79...e+308 | QDoubleSpinBox double 上界，非业务上限 |

### 3.4 扩展指南

**添加新的配置字段**：
1. 在 `config.py` 的 `CrawlerConfig` 数据类中添加字段（带默认值）
2. 在 `__post_init__` 中添加验证（如需要）
3. 在 `to_dict()` / `from_dict()` 中处理
4. 在 GUI `advanced_dialog.py` 中添加表单控件
5. 在 CLI `cli/main.py` 中添加参数（如需要）

**添加新的 UrlOutcome**：
1. 在 `outcomes.py` 的 `UrlOutcome` 枚举中添加成员
2. 在 `is_success` 或 `is_failure` 属性中分类
3. 在对应 Processor 中设置新 outcome（通常在 `_finalize_processor` 或处理该状态的 Processor 中）

**添加新的后台任务**：
1. 在 `engine.py` 的 `AsyncCrawler` 中添加新的 `async` 方法
2. 在 `run()` 方法的 `_bg_tasks` 列表中添加：`asyncio.create_task(self._your_new_task())`
3. 确保方法响应 `self._stop_event` 并正确处理 `CancelledError`

**添加新的排除/过滤逻辑**：
- URL 入队阶段：在 `UrlGate.admit()` 中添加
- HTML 解析阶段：在 `html.py` 的 `extract_links_from_soup()`（链接过滤）或 `extract_text_from_soup()`（文本过滤）中添加
- 使用 `DropReason` 枚举记录新的丢弃原因

### 3.5 依赖版本

**核心依赖**：

| 包 | 最低版本 | 用途 |
|----|---------|------|
| `aiohttp` | 3.9 | 异步 HTTP 客户端 |
| `aiosqlite` | 0.19 | 异步 SQLite |
| `beautifulsoup4` | 4.12 | HTML 解析 |
| `lxml` | 4.9 | XML/Sitemap 解析 |
| `playwright` | 1.40 | 浏览器自动化 |
| `google-re2` | — | 线性时间正则引擎（ReDoS 免疫，硬依赖）|
| `openai` | — | OpenAI 兼容 API 客户端 |
| `PySide6` | 6.5 | GUI 框架 |

**可选依赖**：`orjson`（JSON 加速）、`pydantic` >= 2.0（配置验证）、`psutil`（资源监控）、`pyyaml`（YAML 解析）、`tomllib`（TOML 解析，Python 内置）。

### 3.6 性能特征

- **并发模型**：每个 worker 对应一个浏览器上下文，多个 worker 并行处理不同 URL
- **内存使用**：Chromium 每上下文约 150-300MB；8 并发约 1.2-2.4GB（建议至少 2GB 内存）
- **数据库**：WAL 模式 + NORMAL synchronous，支持高并发读写
- **I/O 缓冲**：JSONL 写入内存缓冲 1MB + 定时刷新 30s，减少磁盘 I/O
- **队列上限**：默认 50000
- **域名状态清理**：定期清理闲置超过 1h 的域名条目，防内存泄漏

### 3.7 打包分发

Nuitka 编译为独立可执行文件的脚本位于 `scripts/build_nuitka.sh` / `scripts/build_nuitka.bat`（暂未随公开发布提供，后续版本计划中）。

---

## 版本历史

| 版本 | 说明 |
|------|------|
| **v0.1.0** | 首次公开发布。从 v5.0.0 内部开发版整理。 |
| v5.0.0 | 内部开发版（模块化重构，未公开发布） |
| v4.6.0 | 单文件原型（`history/astrocrawl_v4.6.0.py`，约 160KB）|
