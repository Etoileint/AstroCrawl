<p align="center">
  <h1 align="center">AstroProject 晚星计划</h1>
  <p align="center"><strong>四包 monorepo — 筑穹 · 摘星 · 织霞 · 天枢</strong></p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License">
  <img src="https://img.shields.io/badge/platform-Linux%20|%20macOS%20|%20Windows-lightgrey" alt="Platform">
  <img src="https://img.shields.io/badge/packages-4-orange" alt="Packages">
</p>

---

# 中文

AstroProject 晚星计划 — 四包 monorepo。乐高式架构，每包可独立 `pip install`，自由组合。

## 包拓扑

```
                    astrobase 天枢 (纯机制层，Python 3.12 stdlib only)
                   ╱    │    ╲
    astroframe 筑穹   astrocrawl 摘星   astroflow 织霞
     (插件平台运行时)   (爬虫引擎)       (工作流编排)

pip 依赖:   astroframe → astrobase,  astrocrawl → astrobase,  astroflow → astrobase
entry_point: astrocrawl → astroframe.plugins,  astroflow → astroframe.plugins
```

## 核心三包

| 包 | 名 | Slogan | 定位 |
|------|----|--------|------|
| **astroframe** | 筑穹 | 筑其基者，擎九霄穹 | 通用插件平台运行时 — entry_points 发现、4 层沙箱防御、sigstore/GPG 签名验证、COLLECTOR/CHAIN 调度 |
| **astrocrawl** | 摘星 | 摘其明者，揽百斗星 | 专业级异步网页爬虫 — Playwright Chromium、robots.txt RFC 9309、声明式规则引擎、AI 多 Provider 底座、PySide6 GUI + CLI |
| **astroflow** | 织霞 | 织其华者，舞万丈霞 | 工作流编排引擎 — 可组合 DAG 工作流（规划中） |

## 组件包

| 包 | 星名 | 定位 |
|------|------|------|
| **astrobase** | 天枢 | 纯机制层 — logfmt 日志、POSIX 原子写入、JSON 兼容层、共享 Protocol |

组件包以北斗七星命名，天枢为首。

## 快速开始

```bash
git clone https://github.com/Etoileint/AstroProject.git
cd AstroProject

python -m venv .venv
source .venv/bin/activate

# 安装全部包（compat 模式 — monorepo 双嵌套布局必需）
pip install --config-settings editable_mode=compat -e astrobase/ -e astroframe/ -e astrocrawl/

# 或按需安装
pip install --config-settings editable_mode=compat -e astrobase/ -e astrocrawl/

playwright install chromium
astrocrawl https://example.com -d 2
```

## 项目结构

```
AstroProject/
├── astrobase/astrobase/       # 天枢 — 纯机制：_logging _atomic _json_compat _types
├── astroframe/astroframe/     # 筑穹 — 插件平台：_registry _lifecycle _scanner _sandbox _host _signature
├── astrocrawl/astrocrawl/     # 摘星 — 爬虫引擎：crawler/ browser/ network/ storage/ rules/ ai/ gui/ cli/ proxy/
├── astroflow/astroflow/       # 织霞 — 工作流引擎（规划中）
├── docs/                      # ADR、开发者指南、领域文档
├── scripts/                   # 代码生成、CI 校验脚本
└── pyproject.toml             # 根配置
```

---

# English

AstroProject 晚星计划 — a four-package monorepo. LEGO-style architecture: each package independently `pip install`-able, freely composable.

## Package Topology

```
                    astrobase (pure mechanism layer, Python 3.12 stdlib only)
                   ╱    │    ╲
          astroframe  astrocrawl  astroflow
         (platform)   (crawler)   (workflow)

pip dependency: astroframe → astrobase,  astrocrawl → astrobase,  astroflow → astrobase
entry_point:    astrocrawl → astroframe.plugins,  astroflow → astroframe.plugins
```

## Core Packages

| Package | Name | Slogan | Role |
|---------|------|--------|------|
| **astroframe** | 筑穹 | 筑其基者，擎九霄穹 | Universal plugin platform runtime — entry_points discovery, 4-layer sandbox, sigstore/GPG verification, COLLECTOR/CHAIN dispatch |
| **astrocrawl** | 摘星 | 摘其明者，揽百斗星 | Professional async web crawler — Playwright Chromium, robots.txt RFC 9309, declarative rule engine, AI multi-provider, PySide6 GUI + CLI |
| **astroflow** | 织霞 | 织其华者，舞万丈霞 | Workflow orchestration engine — composable DAG workflows (planning) |

## Component Package

| Package | Star | Role |
|---------|------|------|
| **astrobase** | 天枢 | Pure mechanism — logfmt logging, POSIX atomic I/O, JSON compat, shared protocols |

Component packages are named after the Big Dipper, starting with 天枢 (Dubhe).

## Quick Start

```bash
git clone https://github.com/Etoileint/AstroProject.git
cd AstroProject

python -m venv .venv
source .venv/bin/activate

pip install --config-settings editable_mode=compat -e astrobase/ -e astroframe/ -e astrocrawl/

playwright install chromium
astrocrawl https://example.com -d 2
```

## Project Structure

```
AstroProject/
├── astrobase/astrobase/       # 天枢 — pure mechanism: _logging _atomic _json_compat _types
├── astroframe/astroframe/     # 筑穹 — plugin platform: _registry _lifecycle _scanner _sandbox _host _signature
├── astrocrawl/astrocrawl/     # 摘星 — crawler engine: crawler/ browser/ network/ storage/ rules/ ai/ gui/ cli/ proxy/
├── astroflow/astroflow/       # 织霞 — workflow engine (planning)
├── docs/                      # ADRs, developer guides, domain docs
├── scripts/                   # Code generation, CI check scripts
└── pyproject.toml             # Root config
```
