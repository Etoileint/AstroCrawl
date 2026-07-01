# 术语映射表 (Terminology Mapping)

AstroCrawl 用户界面术语的权威映射 SSOT。代码以英文为源语言，运行时通过 QTranslator 按目标语言渲染。
`docs/guides/terminology.md` + `astrocrawl/gui/translations/astrocrawl_gui_zh_CN.ts` 共同构成翻译数据。

---

## 翻译原则

- **全翻**：普通用户能直接理解的术语
- **半翻**：保留约定俗成的英文前缀 + 中文后缀（如 Basic 认证、API 密钥）
- **保留**：协议名（HTTP/HTTPS/SOCKS5）、格式名（JSON/HTML/CSS）、产品名（Chromium/SQLite）、行业无等价词的术语（Token/Cookie/Webhook）

---

## 全翻 (Full Translation)

| en (源) | zh_CN (目标) | Context |
|---|---|---|
| URL | 网址 | (通用) |
| Source URL | 源网址 | MainWindow |
| Profile | 配置方案 | (通用) |
| AI Profile | AI 配置方案 | AIProfileEditDialog, _AIProfilePage |
| Proxy Profile | 代理配置方案 | ProxyProfileEditDialog, _ProxyProfilePage |
| Endpoint | 端点 | AI 设置 / 代理设置 |
| Provider | 模型提供商 | AIProfileEditDialog |
| Ready | 就绪 | MainWindow, _ProgressStatusBar |
| Start Crawl | 开始爬取 | MainWindow |
| Pause | 暂停 | MainWindow |
| Resume | 恢复 | MainWindow |
| Stop | 停止 | MainWindow |
| Refresh | 刷新 | MainWindow |
| Crawl | 爬取 | (通用) |
| Depth | 深度 | MainWindow |
| Concurrency | 并发 | MainWindow, AdvancedSettingsDialog |
| Bypass | 直连 | ProxyProfileEditDialog |
| Bypass Domain | 直连域名 | ProxyProfileEditDialog |
| Consumer | 消费者 | _RouteSettingsPage |
| Direct | 直连 | _RouteSettingsPage |
| Layer | 层 | MainWindow (爬取进度) |
| Temperature | 温度 | AIProfileEditDialog |
| Crawl Progress | 爬取进度 | MainWindow |
| Running Log | 运行日志 | MainWindow |
| Basic Config | 基本配置 | MainWindow |
| Test | 测试 | MainWindow |
| Testing... | 测试中... | MainWindow |
| Select | 选择 | MainWindow, AdvancedSettingsDialog |
| Output Path | 输出路径 | MainWindow |
| Proxy Config | 代理配置 | MainWindow |
| Proxy Mode | 代理模式 | MainWindow |
| Prefer Proxy | 优先代理 | MainWindow |
| Prefer Direct | 优先直连 | MainWindow |
| Proxy Only | 仅代理 | MainWindow |
| Direct Only | 仅直连 | MainWindow |
| Same Domain Only | 仅同域名 | MainWindow |
| Respect robots.txt | 遵从 robots.txt | MainWindow |
| Auto-discover Sitemap | 自动发现 Sitemap | MainWindow |
| Advanced Settings | 高级设置 | MainWindow, AdvancedSettingsDialog |
| Rule Management | 规则管理 | MainWindow |
| Save Config | 保存配置 | MainWindow |
| Load Config | 加载配置 | MainWindow |
| Clear Log | 清空日志 | MainWindow |
| No Proxy | 无代理 | MainWindow, ProxyHealthBar |
| Save | 保存 | (通用) |
| Cancel | 取消 | (通用) |
| Confirm | 确认 | (通用) |
| Apply | 应用 | (通用) |
| Delete | 删除 | (通用) |
| Edit | 编辑 | (通用) |
| Add | 添加 | (通用) |
| Remove | 移除 | (通用) |
| Reset | 重置 | (通用) |
| Import | 导入 | (通用) |
| Export | 导出 | (通用) |
| Preview | 预览 | (通用) |
| Validate | 验证 | (通用) |
| Name | 名称 | (通用) |
| Status | 状态 | (通用) |
| Enabled | 启用 | (通用) |
| Search | 搜索 | (通用) |

---

## 半翻 (Partial Translation — 保留约定俗成的英文前缀)

| en (源) | zh_CN (目标) | 理由 |
|---|---|---|
| API Key | API 密钥 | "API" 行业通用 |
| Basic Auth | Basic 认证 | RFC 7617 scheme 名，中文圈约定俗成 |
| Bearer Token | Bearer 凭证 | RFC 6750 scheme 名，与 AI Token 区分 |
| Max Tokens | 最大 Token 数 | "Token" 无合适中文 |
| Webhook URL | Webhook 通知地址 | "Webhook" 行业通用 |

---

## 保留不翻 (Keep English)

协议名 / 格式名 / 产品名 / 高级设置项 / 无等价词术语：

HTTP, HTTPS, SOCKS5, JSON, JSONL, GZip, CSS, CSS Selector, HTML, Chromium, SQLite, Robots.txt, Sitemap, Webhook, Cookie, User-Agent, Token, Temperature(数值), DEBUG, INFO, WARNING, ERROR

---

## 文件对话框

| 项目 | 策略 |
|---|---|
| 对话框标题 | **翻译**（如 "保存配置文件" → "Save Config" → zh_CN "保存配置文件"） |
| 文件过滤器 | **不翻译**（Qt 约定，OS 原生控件） |

---

## Profile 译名说明

"Profile" 译为 "配置方案" 而非 "配置文件"：
- "配置文件" 暗示磁盘文件，而 Profile 是持久化在 Preferences 中的数据结构
- "方案" 暗示一组可选的预设参数组合，贴合使用心智模型
- 参考 Chrome "Person" → 人员；Firefox "Profile" → 配置文件（历史遗留，不理想）

---

## 变更记录

| 日期 | 变更 | 作者 |
|---|---|---|
| 2026-06-27 | 初始版本：完整 en ↔ zh_CN 三档映射表 | - |
