# AstroCrawl 文档

按角色组织的导航索引。

## 新手上路

| 文档 | 说明 |
|------|------|
| [../README.md](../README.md) | 项目概览与快速开始 |
| [../CONTEXT.md](../CONTEXT.md) | 术语表（领域概念定义） |
| [../CONTEXT-COMPANION.md](../CONTEXT-COMPANION.md) | 子系统行为细节与默认值 |
| [guides/developer-guide.md](guides/developer-guide.md) | 开发环境搭建与测试指南 |
| [guides/terminology.md](guides/terminology.md) | 中英双语术语 SSOT（i18n 翻译基准） |

## 架构与决策

| 文档 | 说明 |
|------|------|
| [adr/](adr/) | 架构决策记录（10 条，含状态标注） |
| [adr/README.md](adr/README.md) | ADR 索引与状态一览 |
| [feature-tree.json](feature-tree.json) | 功能模块树状图（<!-- @stats stats.source_files -->115<!-- /@stats --> 文件，<!-- @stats stats.source_lines -->29,059<!-- /@stats --> 行） |
| [../CLAUDE.md](../CLAUDE.md) | AI 辅助开发上下文（架构模式速查） |

## 开发规范

| 文档 | 说明 |
|------|------|
| [guides/gui-standards.md](guides/gui-standards.md) | GUI 编码标准（含 QThread 三层取消模板） |
| [guides/known-issues/](guides/known-issues/) | 平台相关已知问题（非项目 bug） |
| [../SECURITY.md](../SECURITY.md) | 安全策略（原则/范围/限制/报告流程） |

## 审计与质量

| 文档 | 说明 |
|------|------|
| [audit/README.md](audit/README.md) | 审计标准（IEEE 1028 + Google CR + OWASP ASVS） |
| [audit/INDEX.md](audit/INDEX.md) | 审计会话索引 |
| [audit/generate.py](audit/generate.py) | 审计骨架生成脚本 |
| [reports/architecture/](reports/architecture/) | 架构审计报告（4 份活跃 + README） |
| [reports/testing/](reports/testing/) | 测试计划与覆盖审计（3 份历史快照） |
| [reports/archive/](reports/archive/) | 已完成计划与历史审计（~10 份归档） |

## AI Agent 指令

| 文档 | 说明 |
|------|------|
| [agents/domain.md](agents/domain.md) | LLM agent 领域文档消费指令（含自愈逻辑） |
| [agents/issue-tracker.md](agents/issue-tracker.md) | GitHub Issues 操作规范 |
| [agents/triage-labels.md](agents/triage-labels.md) | 工单标签词表映射 |

> `agents/domain.md` 引用 `CONTEXT-MAP.md` 和 `/grill-with-docs`（本仓库不存在）。这些是模板原文引用——文件已有自愈指令（"If any of these files don't exist, proceed silently"），无需人工修正。

## 待开发功能

| 文档 | 说明 |
|------|------|
| [ideas/pending-features.md](ideas/pending-features.md) | 待开发功能思路 |
| [ideas/ai-module-forward-gaps-2026-06-15.md](ideas/ai-module-forward-gaps-2026-06-15.md) | AI 模块远期架构缺口（7 项） |

## 模块文档覆盖矩阵

| 模块 | ADR | 活跃 Reports | Guides | 覆盖评级 |
|------|-----|-------------|--------|---------|
| Kernel | — | — | developer-guide | 基础 |
| Crawler | 0004, 0009 | — | developer-guide | 基础 |
| Browser | — | — | developer-guide, known-issues | 基础 |
| Network | — | — | developer-guide | 基础 |
| Storage | — | — | developer-guide | 基础 |
| Rules Engine | 0005 | post-impl audit, loader/matcher audits | developer-guide | 完整 |
| AI Module | 0006, 0007, 0008 | forward-gaps (→ ideas) | developer-guide | 完整 |
| CLI | — | — | developer-guide | 基础 |
| GUI | — | gui-test-plan, sitemap-robots audit | gui-standards, known-issues | 良好 |
| Proxy | 0010 | — | developer-guide | 良好 |
| Utils | — | — | developer-guide | 基础 |

> **注意**: Storage、Browser、Network、Kernel、CLI、Utils 模块无专项架构报告或 ADR。这是当前覆盖现状的如实反映，非遗漏。如需补齐，应作为独立写作任务而非整理任务。
