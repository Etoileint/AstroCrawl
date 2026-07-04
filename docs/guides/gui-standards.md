# AstroCrawl GUI 实现规范

基于 Apple HIG / Microsoft Fluent UI / GNOME HIG / Material Design 3 + Qt 源码规范制定。
所有新增 GUI 代码必须遵循此文档。

平台相关问题（非项目 bug）见 [`known-issues/`](known-issues/)（PySide6 aarch64 segfault、Sogou IME Qt6 不兼容）。

## 1. 3 层 Design Token 模型

### 1.1 布局 Token (`gui/_tokens.py`)

纯常量模块，零内部依赖。仅 token 化 ≥2 处使用的值。

| Token | 值 | 用途 |
|-------|----|------|
| `SPACE_XS` | 4 | 紧贴（标题栏按钮内、表单垂直、色块-标签） |
| `SPACE_SM` | 6 | 组内（config 组、表单行） |
| `SPACE_MD` | 8 | 默认（根布局、子页面顶部、对话框垂直） |
| `SPACE_LG` | 12 | 窗口边距、表单列间距 |
| `RADIUS_SM` | 3 | 小圆角 |
| `RADIUS_MD` | 4 | 默认圆角 |
| `FONT_SM` | 11 | 辅助信息（outcome 统计） |
| `FONT_MD` | 12 | 正文（URL 状态、ChatML 代码、进度条） |
| `BAR_HEIGHT` | 24 | 状态条统一高度 |
| `PULSE_ANIM_MS` | 33 | 脉动条动画帧间隔（~30 FPS） |
| `PREVIEW_FIELD_COLORS` | 10 色 tuple | 字段高亮调色板，循环分配（规则预览覆盖层注入） |

**规则**: 内容驱动尺寸不 token 化（文本框高度、URL 组最小高度等）。

### 1.2 颜色 Token (`gui/theme.py`)

15 个颜色 Token（`LIGHT_TOKENS` / `DARK_TOKENS`），QPalette 主题引擎。
Key Path: `get_theme_manager().get(key)`。

**QPalette 角色映射：**

| QPalette 角色 | Token | 语义 |
|--------------|-------|------|
| Window | `window_bg` | 主窗口底色 |
| WindowText | `window_text` | 主窗口文字 |
| Button | `button_bg` | 可点击交互控件 |
| ButtonText | `button_text` | 按钮文字 |
| Base | `input_bg` | 面板背景、输入框背景、表格主行、进度条轨道 |
| AlternateBase | `input_bg_alt` | 表格交替行 |
| Text | `input_text` | 输入文字 |
| Highlight | `accent` | 强调/选中/进度填充 |
| Mid | `border` | 边框/分隔 |

未映射到 QPalette 角色的 token（纯 QSS 使用）：

| Token | 语义 | 正确用途 | 禁止用途 |
|-------|------|---------|---------|
| `disabled` | 禁用/次要/辅助文字 | 辅助文本 color、禁用态 | 主内容文字 |
| `success` | 成功/正常 | 状态指示 | 主内容文字 |
| `warning` | 警告 | 状态指示 | — |
| `danger` | 错误/危险 | 状态指示、错误文本 | 主内容文字 |
| `worker_grad_start` | 渐变条起点 | WorkerStatusBar 渐变 | — |
| `worker_grad_end` | 渐变条终点 | WorkerStatusBar 渐变 | — |

命名配对：`input_bg` / `input_bg_alt` 表明交替行关系。

### 1.3 样式工具 (`gui/_style.py`)

消除跨文件重复，DRY 阈值 ≥3 次：

- `primary_button_style() -> str` — `"font-weight: bold;"`
- `status_label_style(bg_color: str) -> str` — 圆角背景 + 内边距 QSS 片段
- `monospace_style() -> str` — 等宽字体 QSS 片段
- `centered_checkbox_container(cb: QCheckBox) -> QWidget` — 表格中居中 check box

**规则**: 不做全局 QSS 文件（会覆盖 QPalette 主题）。

## 2. 命名规范

### 2.1 按钮变量 — `_` 前缀（PEP 8 私有）

所有按钮变量以 `_` 开头：`self._run_btn`、`self._stop_btn` 等。

### 2.2 控件命名 — kebab-case `setObjectName`

仅对 3 类控件命名（CSS 选择 / 调试 / 测试），kebab-case 格式：
`"run-btn"`, `"stop-btn"`, `"url-input"`, `"log-list"`, `"theme-btn"`, `"search-input"`, `"rule-table"`

普通布局辅助控件不命名。

### 2.3 文件命名 — `_` 前缀私有模块

`_tokens.py`, `_style.py` 等。

### 2.4 信号命名

**规则**: 对标 Qt 源码。离散事件 → 过去分词；持续状态变化 → 名词 + Changed/Progress。

| 事件类型 | 命名模式 | 示例 |
|----------|---------|------|
| 离散事件 | 过去分词 | `message_logged`, `error_occurred`, `stats_updated`, `rule_matched` |
| 持续状态 | 名词 + Changed | `pause_changed`, `worker_state_changed`, `theme_changed` |
| 进度 | 名词 + Progress | `layer_progress` (对标 `QNetworkReply.downloadProgress`) |
| 完成 | finished/done 形容词 | `finished`, `session_done` |

**禁止** `_signal` 后缀。保留不改的信号：`layer_progress`, `finished`, `session_done`, `rule_generated`, `confirmed`, `theme_changed`。

## 3. QMessageBox 分类法

| 类型 | 图标 | 语义 | 使用场景 |
|------|------|------|---------|
| `QMessageBox.Warning` | ⚠ | 不可逆操作确认 | 删除、重置、隐私数据发送 |
| `QMessageBox.Critical` | ✕ | 系统级错误 | 文件写入失败、网络错误 |
| `QMessageBox.Information` | ℹ | 无风险的交互指引 | 请先选择一条规则、请先选择目录 |
| **非模态状态栏** | — | 成功/错误通知 | 验证通过/失败、导入成功、导出完成 |

### 3.1 不可逆操作确认模板

```python
msg = QMessageBox(QMessageBox.Warning, "确认删除",
                  f"确定要删除规则 '{name}' 吗？", parent=self)
del_btn = msg.addButton("删除", QMessageBox.YesRole)
msg.addButton("取消", QMessageBox.NoRole)
msg.exec()
if msg.clickedButton() != del_btn:  # 必须显式对象比较，不能用 exec() 返回值
    return
```

### 3.2 成功/错误通知模板

```python
def _show_status(self, msg: str, level: str = "success") -> None:
    """更新状态栏文本和颜色编码（持久，新消息替换旧消息）。

    Args:
        msg: 状态消息（单行）
        level: "success" | "warning" | "error"
    """
    self._status_level = level
    self._status_bar.setText(msg)
    theme = get_theme_manager()
    color_map = {
        "success": theme.get("success"),
        "warning": theme.get("warning"),
        "error": theme.get("danger"),
    }
    fg = color_map.get(level, theme.get("success"))
    self._status_bar.setStyleSheet(
        status_label_style(theme.get("input_bg")) + f"color: {fg}; font-weight: bold;"
    )
```

### 3.3 子页面向上通信

通过 `status_callback` 参数注入，不依赖父级存在。详见 **§13.6 子页面→父级通信**。

```python
class _RuleTablePage(QWidget):
    def __init__(self, cfg, status_callback=None, parent=None):
        self._show_status = status_callback or (lambda msg, level="success": None)
```

## 4. 对话框尺寸 — adjustSize() 锁定模式

所有对话框构造遵循此模板（对齐 4 家 HIG）：

```python
def __init__(self, parent=None):
    super().__init__(parent)
    self.setWindowTitle("...")
    self._setup_ui()
    self.adjustSize()
    ideal_h = self.height()
    self.setMaximumWidth(self.width())
    screen = self.screen()
    if screen:
        max_h = int(screen.availableGeometry().height() * 0.85)
        self.setMaximumHeight(min(ideal_h, max_h))
        self.setMinimumHeight(min(ideal_h, max_h))
    else:
        self.setMaximumHeight(ideal_h)
        self.setMinimumHeight(ideal_h)
    self.setMinimumWidth(self.width())
```

**设计理由**：理想尺寸 ≤ 85% 屏幕高度时，锁定到理想尺寸（所有内容可见、无滚动条）；理想尺寸 > 85% 时，锁定到 85% 上限，内部 QScrollArea 出现滚动条。条件式设定避免 `setMinimumHeight > setMaximumHeight` 冲突。

特殊处理：
- **含文本域的对话框**（ChatMLPreviewDialog）：`setSizeGripEnabled(True)`
- **含 QTabWidget 的对话框**（RulesDialog, AdvancedSettingsDialog）：`adjustSize()` 前确保切换到内容最多的 tab、调用 `_on_theme_changed()`（见 §9）
- **含表格的对话框**（CompletionReportDialog）：表格 `layout.addWidget(table, 1)` + 表格内置滚动条处理溢出，头信息和按钮保持固定。`setSizeGripEnabled(True)` 允许用户缩小窗口

## 5. 按钮行横向响应式均分布局

所有纯按钮行（一行内仅含按钮，无其他控件）采用横向响应式均分布局。核心原则：

- **普通均分**: 所有按钮 `addWidget(btn, 1)`，等宽拉伸。
- **特殊比例**: 允许有明确语义的比例（如 3:1 主次按钮）。
- **禁止 `addStretch()`**: 不使用 stretch 分隔左右按钮组。

```
均分示例:  [应用]  [取消]  [确认]         ← 1:1:1
比例示例:  [生成并导入        ]  [配置 AI]  ← 3:1
```

### 5.1 QGridLayout 工具栏

按钮工具栏采用 QGridLayout 时，需为每列设置 `setColumnStretch(col, 1)` 实现等宽均分。搜索栏等长 span 控件自动对齐上方按钮列宽。

### 5.2 表单行比例规则

设置对话框的 QGroupBox 内，表单行采用横向响应式比例分配。核心规则：

**默认 1:1:1:1 四均分**。一行中每个控件占 1 份，总份数 4。普通控件（标签/按钮/复选框）固定各占 1，框类（`QLineEdit`/`QSpinBox`/`QDoubleSpinBox`/`QComboBox`/`QTextEdit`）占剩余全部份数。

| 行类型 | 比例 | 调用示例 |
|--------|------|---------|
| 单控件（checkbox） | 占整行 | `_form_row(widget=cb)` |
| label + 框类 | 1:3 | `_form_row("页面超时", spin)` |
| label + 框类 + 按钮 | 1:2:1 | `_form_row("日志文件", [edit, btn], 1, 2, 1)` |
| label + checkbox + 框类 | 1:1:2 | `_form_row("最大运行时间", [cb, spin], 1, 1, 2)` |

**禁止 `addStretch()`**——所有空间由显式 stretch 分配。

#### `_form_row` 辅助函数

设置对话框内使用 `_form_row()` 替代 `QFormLayout.addRow()`，实现上述比例规则：

```python
def _form_row(label_text: str = "", widget=None, *stretches: int) -> QHBoxLayout:
    """创建横向响应式 QHBoxLayout 行。

    - 仅 widget（label_text=""）→ 占整行
    - label + 单 widget → 1:3（label=1, field=3）
    - label + 多 widget → 显式 stretches（总份数=4: label/btn=1, 框类=剩余）
    """
    row = QHBoxLayout()
    row.setSpacing(SPACE_SM)
    if not label_text:
        row.addWidget(widget, 1)
        return row
    lbl = QLabel(label_text)
    lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    if not stretches:
        row.addWidget(lbl, 1)
        if isinstance(widget, (list, tuple)):
            row.addWidget(widget[0], 3)
        else:
            row.addWidget(widget, 3)
        return row
    it = iter(stretches)
    row.addWidget(lbl, next(it))
    if isinstance(widget, (list, tuple)):
        for w, s in zip(widget, it):
            row.addWidget(w, s)
    else:
        row.addWidget(widget, next(it))
    return row
```

QGroupBox 内替换 QFormLayout 为 QVBoxLayout + `_form_row` 行：

```python
group = QGroupBox("超时与限制")
gl = QVBoxLayout()
gl.setSpacing(SPACE_SM)
gl.addLayout(_form_row("页面超时 (ms)", self.page_timeout))       # 1:3
gl.addLayout(_form_row("最大运行时间", [cb, spin], 1, 1, 2))      # 1:1:2
gl.addLayout(_form_row(widget=self.follow_nofollow))              # 全宽
group.setLayout(gl)
```

## 6. ThemeManager 访问

**统一全局访问，禁止假 DI**：

```python
from astrocrawl.gui.theme import get_theme_manager
theme = get_theme_manager()
```

不要在构造器中传递 `theme_manager` 参数。`get_theme_manager()` 是单例。

## 7. 其他规则

- **Qt Fusion 风格**: `app.setStyle("Fusion")` 在 `main.py` 中，跨平台一致
- **Qt Offscreen 测试**: `QT_QPA_PLATFORM=offscreen`，所有 GUI 测试标记 `@pytest.mark.gui`
- **Qt fixture 清理**: 创建 QWidget 子类的 fixture 必须通过 `request.addfinalizer(page.deleteLater)` 注册清理，否则 QScrollArea / QGroupBox 等深层嵌套控件在 offscreen 模式下累积导致 segfault
- **主窗口初始尺寸**: `adjustSize()` 由布局自决，`setMinimumWidth(420)` 保底，居中 `move(screen_center - rect.center())`。禁止硬编码 `resize()` 屏幕百分比。
- **信号生命周期**: `dispose()` 注册表驱动，connect/disconnect 自动对称
- **TitleBar 封装**: 外部不穿透访问内部 WorkerStatusBar，通过 `connect_worker_state(session)` 公开方法

## 8. QPalette 与显式 QSS 分界原则

**核心原则：QPalette 能正确覆盖的标准控件不额外加 QSS。**

Qt Fusion 风格通过全局 QPalette 自动传播颜色。以下标准控件无需显式 QSS：
QPushButton、QLineEdit、QTextEdit、QLabel、QComboBox、QCheckBox、QSpinBox、
QDoubleSpinBox、QTableView、QTableWidget、QListWidget、QGroupBox、QScrollArea、QTabBar。

显式 QSS 仅在以下两种情况合法：

| 类别 | 说明 | 示例 |
|------|------|------|
| **A. 非 QPalette 属性** | QPalette 不可表达的 CSS 属性 | `border-radius`, `padding`, `font-weight`, `font-size`, `text-align` |
| **B. 语义覆盖** | QPalette 默认颜色在此处语义错误 | `color: danger`（错误文本）、`background-color: input_bg`（QLabel badge 背景）|

**禁止**：QSS 值等于对应 QPalette 角色当前 token 的冗余覆盖。

此原则的推论：新增代码中，如果需要对标准控件设置 `background-color`/`color`，首先问"QPalette 是否已经给了正确的值？"——如果是，不写；如果不是，属于 B 类语义覆盖，合法，但须连接 `theme_changed` 重刷。

## 9. QTabWidget 主题模式

任何含 QTabWidget 的对话框/窗口必须遵循此模板：

```python
def __init__(self, ...):
    ...
    self._tabs = QTabWidget()
    self._theme_mgr = get_theme_manager()
    ...
    # 所有 tab 添加完毕后、adjustSize() 之前：
    if self._theme_mgr is not None:
        self._theme_mgr.theme_changed.connect(self._on_theme_changed)
    self._on_theme_changed()

def _on_theme_changed(self) -> None:
    if self._theme_mgr is None:
        return
    bg = self._theme_mgr.get("input_bg")
    # QPalette on each page widget — avoids QSS disrupting QTableView QPalette
    for i in range(self._tabs.count()):
        page = self._tabs.widget(i)
        if page is not None:
            p = page.palette()
            p.setColor(QPalette.Window, QColor(bg))
            page.setPalette(p)
            page.setAutoFillBackground(True)
            page.setAttribute(Qt.WA_InputMethodEnabled, True)
```

**设计理由：**

- **禁止**在 QTabWidget 上调用 `setStyleSheet()` — 会强制所有子控件（含 QTableView）进入 QSS 渲染模式，阻断 `QPalette::Base`/`AlternateBase` 向下的传播，导致表格交替行色失效
- 改为对每个 tab 页 widget 独立逐页设置 `QPalette::Window` + `setAutoFillBackground(True)`。子控件（QScrollArea 视口、QTableView 视口）通过各自 `QPalette::Base`（= `input_bg`）自动渲染，与页面背景天然统一
- `WA_InputMethodEnabled` 与 `setAutoFillBackground(True)` 配对设置——声明此 tab page 包含文本输入控件，在所有平台上无害且语义正确
- 此方案零 QSS、零 palette 阻断、零特殊处理——所有标准控件走同一条 QPalette 路径
- QTableView 交替行通过 `QPalette::Base`/`AlternateBase`（`input_bg`/`input_bg_alt`）自动渲染
- `QTabBar`（tab 按钮）不添加显式 QSS — `QPalette::Button` → `button_bg` 语义正确
- `QTabWidget::pane`（tab 外框，1-2px）走原生 QPalette::Window = `window_bg`，内容区填充后视觉差异可忽略

**四步防护模式**（MainWindow / RulesDialog / AdvancedSettingsDialog / TitleBar 一致使用）：

1. `self._theme_mgr = get_theme_manager()`
2. `if self._theme_mgr is not None: connect theme_changed`
3. 手动调用一次 `self._on_theme_changed()`
4. handler 内 `if self._theme_mgr is None: return` 防护

## 10. 对话框分类与滚动策略

| 类别 | 特征 | 滚动方案 | 实例 |
|------|------|---------|------|
| **表单类页面** | QFormLayout / QGroupBox 分区，控件持续增长 | QScrollArea 外包 | AdvancedSettingsDialog（常规设置/全局设置 tab），RulesDialog（自定义 tab） |
| **表格类页面** | 数据表格为主内容 | 表格内置滚动条 + `layout.addWidget(table, 1)` | AdvancedSettingsDialog（AI 设置 tab），RulesDialog（规则列表/远程源 tab），CompletionReportDialog |
| **独立设置窗口** | 无 QTabWidget，固定头部 + 可滚内容 | QScrollArea 外包滚动区，固定控件外置 | ThemeDialog（模式选择外置，色块内滚） |
| **固定表单对话框** | 固定字段数 | `adjustSize()` 锁定 | AIProfileEditDialog，RuleEditDialog |
| **确认对话框** | 简单确认/警告 | QMessageBox | 删除确认，错误提示 |

**说明**：AdvancedSettingsDialog 和 RulesDialog 混合包含表单类页面和表格类页面。滚动机制不同——表单类用外层 QScrollArea，表格类用自带滚动条。所有页面背景通过 §9 统一为 `input_bg`。

**表单类页面**特征：控件持续增长，面向未来。QScrollArea 视口通过 `QPalette::Base` 自动获得 `input_bg`，与 §9 逐页设置的 `QPalette::Window`（= `input_bg`）天然统一。无需逐控件单独设置。

**表格类页面**的 QTableView/QTableWidget **禁止**包在 QScrollArea 中——双滚动条冲突、头信息被滚走、虚拟滚动优化丧失。表格本身是 QAbstractScrollArea 子类，自带视口与滚动条。页面背景通过 §9 统一为 `input_bg`。

**独立设置窗口**（ThemeDialog）：固定控件（模式选择 QGroupBox）放在 QScrollArea 外部，滚动内容（色块 QGroupBox）放在内部。窗口高度走 §4 的 85% 兜底模式。

### 10.1 QTabWidget 内嵌标签页 — 三种标准模式

含 QTabWidget 的对话框内，每个标签页必须遵循以下模式之一：

**纯表单模式**（QFormLayout）：
```python
from astrocrawl.gui._style import create_form_scroll_area

tab = QWidget()
scroll = create_form_scroll_area()
form_widget = QWidget()
form_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
layout = QFormLayout(form_widget)
layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
# ... 添加控件 ...
scroll.setWidget(form_widget)
tab_layout = QVBoxLayout(tab)
tab_layout.setContentsMargins(0, 0, 0, 0)
tab_layout.addWidget(scroll)
tabs.addTab(tab, "标签名")
```

**分区表单模式**（QGroupBox 分区，适用于表单+面板混排）：
```python
from astrocrawl.gui._style import create_form_scroll_area

tab = QWidget()
scroll = create_form_scroll_area()
inner = QWidget()
inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
inner_layout = QVBoxLayout(inner)
inner_layout.setContentsMargins(0, SPACE_MD, 0, 0)
inner_layout.setSpacing(SPACE_MD)
# ── QGroupBox 分区 ──
group = QGroupBox("分区标题")
group_layout = QVBoxLayout(group)
group_layout.setSpacing(SPACE_SM)
# ... 添加该分区的控件 ...
inner_layout.addWidget(group)
# ... 更多分区 ...
scroll.setWidget(inner)
tab_layout = QVBoxLayout(tab)
tab_layout.setContentsMargins(0, 0, 0, 0)
tab_layout.addWidget(scroll)
tabs.addTab(tab, "标签名")
```

**表格模式**：
```python
table = create_managed_table(...)
# 不包 QScrollArea，表格自带滚动条
tabs.addTab(table, "标签名")
```

所有模式页面背景通过 §9 的逐页 QPalette 机制自动统一为 `input_bg`，无需各自设置。

**QScrollArea 使用规则**：表单类页面必须通过 `create_form_scroll_area()` 工厂创建 QScrollArea，禁止内联 QScrollArea 构造。工厂统一处理 widgetResizable + 隐藏横向滚动条 + NoFrame，并内置 viewport 级 IME 属性（`WA_InputMethodEnabled`），确保 Linux/Wayland + fcitx5/ibus 下中文输入法正常工作。

**防御性规则**：所有放入 `QScrollArea(widgetResizable=True)` 的表单/分组/色块 widget 必须设置 `setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)`——防止内容行数稀疏时 QScrollArea 垂直拉伸行间距。此规则对 QFormLayout 纯表单、QGroupBox 分区、QFormLayout 色块均适用。

## 11. 特定控件与 Fail-Fast 规则

### 11.1 QProgressBar

```python
bar.setStyleSheet(
    f"QProgressBar {{ border-radius: {RADIUS_MD}px; text-align: center; "
    f"background-color: {theme.get('input_bg')}; }}"
    f"QProgressBar::chunk {{ background-color: {theme.get('accent')}; "
    f"border-radius: {RADIUS_MD}px; }}"
)
```

轨道 `background-color` 使用 `input_bg`（语义：待填充的空白槽位），不是 `button_bg`。
chunk 使用 `accent`（`QPalette::Highlight` 语义正确）。
`border-radius` 和 `text-align` 属于 §8 A 类 QSS。

### 11.2 颜色获取

禁止硬编码颜色值（`"red"`, `"#FF0000"`）或 ThemeManager 未初始化时的静默回退值。
`get_theme_manager()` 正常流程中始终可用。不可用时 crash 暴露问题（fail-fast），不静默吞回退。

## 12. 控件生命周期与动画控件

### 12.1 动画条合约

所有脉动/渐变动画控件（`WorkerStatusBar`、`_PulseBar` 等）必须继承 `_AnimatedBar`（`gui/_animated_bar.py`）。

Template Method 合约：
- 子类实现 `_paint_bar(painter, anim_offset, w, h)` — 实际绘制逻辑。**禁止覆盖 `paintEvent`**。
- 子类覆盖 `_tick()` — 动画步进（默认固定步长 0.008）。
- 子类覆盖 `_on_stop()` — 自定义状态清理。`stop()` 调用此钩子后统一执行 `repaint()`。

### 12.2 清理顺序

父控件销毁前（`closeEvent`、`reject()`、`dispose()`），按以下顺序清理：

```
1. stop() 动画控件  → _on_stop() 清理状态 → repaint() 同步绘制
2. _cleanup_worker() 后台线程 — cancel() → wait(N) → terminate() 兜底（详见 §12.5）
3. super().reject() / close() / dispose()
```

MainWindow 中会话级资源的停止统一通过 `_cleanup_session()` 入口（SSOT），`_on_thread_finished` / `closeEvent` else 分支 / `_force_close_crawler` / `_reset_app` 均调用之。`_cleanup_session()` 幂等，可被多次安全调用。

`stop()` 使用 `self.repaint()`（**同步**绘制）而非 `self.update()`（**异步** QPA 事件）。异步 paint event 在控件析构后通过 QPA 后端投递时，`QPainter` 构造调用 `QPaintDevice::paintEngine()` 纯虚函数 → SIGABRT。

### 12.3 对话框关闭检查清单

创建包含动画控件的对话框时：

- 必须覆写 `reject()`（窗口 X 按钮和 `Esc` 键都走 `reject()`）
- `reject()` 中停止所有活跃的动画控件
- `_on_cancel()` 调用 `self.reject()` 而非直接 `super().reject()`
- 示例：
  ```python
  def reject(self) -> None:
      self._pulse_bar.stop()
      self._custom_page.reset()   # 停止后台动画
      super().reject()
  ```

### 12.4 QTimer 生命周期规则

- 动画定时器**必须**在 `stop()`/`reject()` 中显式停止，**不得**依赖父子析构自动清理。
- 原因：定时器停用后，其最近一次 `timeout` 回调（`_tick()`）的 `self.update()` 已在 QPA paint queue 中留下一个 paint event。该 event 在控件析构后投递会导致崩溃。
- 创建 `QTimer` 时必须传递 `self` 作为父对象（`QTimer(self)`），由 Qt 在控件析构时自动销毁 C++ 定时器对象。

### 12.5 QThread Worker 三层协作式取消

**所有 GUI QThread worker 必须实现此标准。** 三层缺一不可——缺少任何一层，线程在阻塞 I/O 期间无法被可靠终止，导致对话框关闭时 widget 树析构连坐销毁运行中的 QThread → `QThread: Destroyed while thread is still running` → `Fatal Python error: Aborted`。

#### 三层定义

| 层 | 职责 | 机制 |
|----|------|------|
| 信号层 | 通知线程"请停止" | `QThread.requestInterruption()` / `threading.Event.set()` |
| 检查层 | 线程在安全点响应并退出 | `isInterruptionRequested()` / `cancel_event.is_set()` / asyncio `await` |
| 打断层 | 解除阻塞 I/O 强制线程从系统调用返回 | `client.close()` 关闭 HTTP 连接池 / `loop.call_soon_threadsafe(task.cancel)` |

#### 模板 A — 同步阻塞 HTTP

适用于裸 `httpx`/`requests` 同步调用（`ssl.read()` 阻塞，线程卡死在 C 扩展内）。

```python
class _SyncWorker(QThread):
    def __init__(self, ...):
        super().__init__(parent)
        ...
        self._cancel_event = threading.Event()
        self._client: AIClient | None = None

    def run(self):
        try:
            self._client = AIClient(...)
            result = generator.generate_sync(..., cancel_event=self._cancel_event)
            if not self.isInterruptionRequested():
                self.finished.emit(result)
        except GenerationCancelled:
            return
        except Exception as e:
            self.error_occurred.emit(str(e))

    def cancel(self):
        self._cancel_event.set()          # 信号层
        self.requestInterruption()        # 信号层
        if self._client is not None:
            self._client.close()          # 打断层 — 关闭 httpx 连接池，ssl.read() 立即抛 IOError
```

**cleanup 侧**（所属页面 `_cleanup_worker()`）：

```python
def _cleanup_worker(self):
    w = self._worker
    if w is None:
        return
    if not w.isRunning():
        self._worker = None
        # 手动恢复 UI 状态（幂等操作；worker 已自然完成但 handler 因信号断开未触发）
        return
    # 断开页面持有的信号槽
    for sig_name in SIGNAL_NAMES:
        try:
            getattr(w, sig_name).disconnect()
        except (RuntimeError, AttributeError):
            pass
    w.cancel()
    if not w.wait(30000):                # AI 生成最长，给 30s
        w.terminate()
        w.wait(2000)
    self._worker = None
    # 手动恢复 UI 状态（busy_changed、按钮等，与 completion handler 恢复内容一致）
```

#### 模板 B — asyncio 网络 I/O

适用于 `asyncio.new_event_loop()` + `run_until_complete()` 模式（`await` 点是天然检查点，`CancelledError` 毫秒级响应）。

```python
class _AsyncWorker(QThread):
    def __init__(self, ...):
        super().__init__(parent)
        ...
        self._main_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            if self.isInterruptionRequested():   # 检查层 — cancel() 可能在 task 创建前被调用
                return
            main_task = asyncio.ensure_future(self._work(), loop=loop)
            self._main_task = main_task
            result = loop.run_until_complete(main_task)
            self.finished.emit(result)
        except asyncio.CancelledError:           # 检查层 — task.cancel() 在下一个 await 抛出
            pass
        except Exception as e:
            self.error_occurred.emit(str(e))
        finally:
            # cancel pending tasks + loop.close()
            self._loop = None
            self._main_task = None

    def cancel(self):
        self.requestInterruption()                              # 信号层
        if self._main_task and self._loop and self._loop.is_running():
            try:
                self._loop.call_soon_threadsafe(self._main_task.cancel)  # 打断层
            except RuntimeError:            # event loop 恰在此时关闭（TOCTOU 竞态防护）
                pass
```

**cleanup 侧**：

```python
def _cleanup_worker(self):
    w = self._worker
    if w is None:
        return
    if not w.isRunning():
        self._worker = None
        # 手动恢复 UI 状态
        return
    # 断开所有页面持有的信号槽（disconnect() 无参断开全部，包括 lambda）
    for sig_name in SIGNAL_NAMES:
        try:
            getattr(w, sig_name).disconnect()
        except (RuntimeError, AttributeError):
            pass
    w.cancel()
    if not w.wait(5000):                 # asyncio 1-3s 内必定完成
        w.terminate()
        w.wait(2000)
    self._worker = None
    # 手动恢复 UI 状态
```

**同步函数统一**：如 worker 内混用 sync/async 调用，sync 函数用 `asyncio.to_thread()` 包装以统一取消路径。

#### 模板 C — 纯文件 I/O

毫秒级本地操作，无需打断层。仅在入口加 `isInterruptionRequested()` 检查。

```python
class _FileWorker(QThread):
    def run(self):
        if self.isInterruptionRequested():
            return
        result = do_file_io(...)
        self.finished.emit(result)

    def cancel(self):
        self.requestInterruption()
```

#### 模板 C+ — 批量文件 I/O（cancel_event 协作式取消）

适用于需要处理大量文件（> 50 个）的同步 I/O worker。与模板 C 的区别在于函数内部每条记录处理后检查 `cancel_event.is_set()`，可实现亚秒级取消响应。

```python
class _BatchFileWorker(QThread):
    def __init__(self, ...):
        super().__init__(parent)
        ...
        self._cancel_event = threading.Event()

    def run(self):
        if self.isInterruptionRequested():
            return
        results = process_files(..., cancel_event=self._cancel_event)
        if self.isInterruptionRequested() or self._cancel_event.is_set():
            return
        self.finished.emit(results)

    def cancel(self):
        self.requestInterruption()
        self._cancel_event.set()
```

被调用函数需接收 `cancel_event` 参数并在循环中检查 `cancel_event.is_set()`。

#### Worker / Page 契约

| 角色 | 必须实现 |
|------|---------|
| Worker | `cancel()` 方法 — 三层全部触发 |
| 所属页面 | `_cleanup_worker()` 方法 — 断信号 → `w.cancel()` → `w.wait(N)` → `terminate()` 兜底 → 手动恢复 UI 状态 |
| 所属对话框 | `reject()` 和 `accept()` 均调用各子页面的 `_cleanup_worker()`，在 `super().reject()/accept()` 之前 |

**`_cleanup_worker()` 幂等性**：三分支入口——`w is None` → return；`not w.isRunning()` → 清空引用 + 恢复 UI → return；running → 断信号 → cancel → wait → terminate → 清空引用 + 恢复 UI。重复调用时 `disconnect()` 抛 `RuntimeError`，`try/except` 静默。

**UI 状态手动恢复**：`_cleanup_worker()` 断开 `finished`/`error_occurred` 信号后，正常回调不触发。所有由这些回调执行的状态恢复（`busy_changed(False)`、按钮启用、`_fetching`/`_probe_worker` 引用清除）必须在 `_cleanup_worker()` 中手动执行。

### 12.6 QThread Worker 生命周期模式

定义 QThread worker 从创建到销毁的完整生命周期规范。与 §12.5（如何安全终止运行中的线程）正交——§12.5 关注取消机制，§12.6 关注所有权和引用管理。

#### 12.6.1 模式定义

全项目统一使用**显式生命周期（Mode 2）**：worker 的 C++ 对象生命周期完全由持有页面的 Python 引用控制。不使用 `finished → deleteLater` 自清理模式（PySide6/Shiboken 绑定层中 `deleteLater` 销毁 C++ 对象后 Python 包装器仍存活，任何方法调用抛出 `RuntimeError`）。

#### 12.6.2 创建契约

```python
# 禁止：finished/error_occurred 连接 deleteLater
# worker.finished.connect(worker.deleteLater)    ← 全项目禁止

# 正确：
self._worker = XxxWorker(parent=self)
self._worker.finished.connect(self._on_result)
self._worker.start()
```

#### 12.6.3 完成 handler 契约

完成 handler **第一行必须是 `self._worker = None`**。此规则确保引用在任何代码路径（包括异常路径）中都被清空。对于使用 lambda 独立连接的场景（如 `_ai_profile_page.py`），`setattr(None)` 必须在 `finished` 信号链上、位置优先于其他业务 handler——Qt 信号发射遍历连接列表，单个 slot 异常不阻断后续 slot 执行，因此 lambda 方案天然免疫业务 handler 异常。

```python
# 命名 handler 模式（推荐）
def _on_result(self, result):
    self._worker = None          # 第一行，无条件执行
    self.busy_changed.emit(False)
    ...

# lambda 模式（复杂场景，多信号连接时更清晰）
worker.finished.connect(lambda: setattr(self, "_worker", None))
worker.finished.connect(self._on_result)
```

#### 12.6.4 `_cleanup_worker` 三分支模板

```python
def _cleanup_worker(self) -> None:
    w = self._worker_attr
    if w is None:
        return
    if not w.isRunning():
        # handler 因信号已断开未触发（用户关闭对话框时 worker 恰好完成）
        # 需手动恢复 UI 状态（全部操作幂等，重复调用无害）
        self._worker_attr = None
        # restore UI state
        return
    # worker 正在运行 — 断开所有页面持有的信号槽
    for sig_name in SIGNAL_NAMES:
        try:
            getattr(w, sig_name).disconnect()
        except (RuntimeError, AttributeError):
            pass
    w.cancel()
    if not w.wait(TIMEOUT):
        w.terminate()
        w.wait(2000)
    self._worker_attr = None
    # restore UI state（与 not running 分支完全相同的恢复操作）
```

#### 12.6.5 Dialog 契约

对话框 `accept()` 和 `reject()` 在调用 `super().accept()/reject()` 之前必须调用各子页面的 `_cleanup_worker()`。

#### 12.6.6 新增 Worker 检查清单

1. Worker 有 `cancel()` 方法，按 §12.5 要求实现对应模板的三层取消
2. Worker 的 `run()` 中有适当的 `isInterruptionRequested()` 或 `cancel_event.is_set()` 检查点
3. 页面将 worker 存储为实例属性（`self._worker`）
4. 页面的完成/错误 handler 第一行清空 worker 引用，或使用独立 lambda 连接在 `finished` 信号链上
5. 页面有 `_cleanup_worker()` 方法，使用 §12.6.4 三分支模板
6. 对话框的 `accept()` 和 `reject()` 在 `super()` 之前调用各子页面的 `_cleanup_worker()`
7. 创建 worker 的入口有防重入守卫（`if self._worker is not None: return` 或等价逻辑）
8. Worker 创建处**禁止** `finished.connect(worker.deleteLater)` 和 `error_occurred.connect(worker.deleteLater)`
9. 异步 worker 的 `cancel()` 中 `call_soon_threadsafe` 包 `try/except RuntimeError`

### 13. 通知与反馈体系

基于 Apple HIG（Alerts / Inline Validation）、Material Design 3（Snackbar / Dialog / Banner / Text Field Error）、Microsoft Fluent UI（MessageBar / ContentDialog / InfoBar）、GNOME HIG（Toasts / Info Bars / Dialogs）四家共识制定。

通知机制按**持续性 × 模态 × 作用域 × 紧急度**四维正交分类后确定选型。共五种机制，覆盖所有桌面 GUI 通知场景。

### 13.1 通知分类法

| # | 机制 | 持续性 | 模态 | 作用域 | 紧急度 |
|---|------|--------|------|--------|--------|
| A | QMessageBox | 持久（用户关闭） | **模态** | 窗口级 | 警告 / 错误 / 信息 |
| B | 持久状态栏（含脉动条） | 持久 / 条件 | 非模态 | 窗口级 | 全部四级 |
| C | 内联字段验证 | 条件（仅错误时） | 非模态 | **字段级** | 错误 |
| D | 按钮内联反馈 | 条件（操作期间） | 非模态 | **控件级** | 信息 |
| E | 子页面→父级通知 | 视目标而定 | 非模态 | **跨层级** | 全部四级 |

瞬态自动消失通知（Snackbar / Toast）在 Material Design 3、Fluent UI、GNOME 中均有对应模式，但本项目**有意不采用**——爬虫工具需要用户回看状态历史文本，自动消失反而损耗信息。

### 13.2 QMessageBox — 模态警告

准入条件：用户必须明确回应方可继续。

详细模板见 **§3 QMessageBox 分类法**。补充决策表：

| 情况 | 使用 | 反例 |
|------|------|------|
| 不可逆操作确认（删除/重置/覆盖） | `QMessageBox.Warning` + 显式按钮对象比较 | — |
| 系统级错误，当前操作无法继续 | `QMessageBox.Critical` | ~~输入格式错误~~ |
| 引导用户提供缺失输入 | `QMessageBox.Information` | ~~操作成功通知~~ |
| 操作成功/失败通知 | **禁止** QMessageBox → 走 13.3 状态栏 | — |

### 13.3 持久状态栏（含脉动条）— 主界面与测试语义对话框

`_ProgressStatusBar` 复合组件将脉动动画条（`_PulseBar`）与状态标签（QLabel）封装为单个控件，常驻于内容区与按钮行之间。

#### API

```python
from astrocrawl.gui._animated_bar import _ProgressStatusBar

# ── 构造 ──
self._psb = _ProgressStatusBar()
self._psb.show_status("就绪")
layout.addWidget(self._psb)

# ── 接线子页面（自动检测 busy_changed + status_message 信号） ──
self._psb.connect_page(self._ai_page)

# ── 异步操作 ──
self._psb.start_pulse()
self._psb.show_status("正在测试...", "info")

# ── 结果 ──
self._psb.stop_pulse()
self._psb.show_status("测试通过", "success")

# ── 对话框关闭前 ──
def reject(self) -> None:
    self._psb.dispose()
    super().reject()
```

`show_status_bar=False`：隐藏状态标签，用于页面自带状态栏的容器（AdvancedSettingsDialog）。

#### 四级颜色语义

| level | token | 使用场景 |
|-------|-------|---------|
| `"info"` | `window_text` | 进行中（"正在拉取模型列表…"、"正在测试 192.168.1.1:8080…"） |
| `"success"` | `success` | 就绪、操作完成 |
| `"warning"` | `warning` | 警告、部分失败 |
| `"error"` | `danger` | 操作失败 |

`_ProgressStatusBar._show_status(msg, level)` 已内置该映射（`_animated_bar.py:191-195`）。MainWindow 独立 `_show_status` 方法共享同一四级颜色语义。

#### 适用范围

有且仅有具备**测试、验证及类似语义**的对话框才配备 `_ProgressStatusBar`。此类语义定义为：对话框内存在需要异步等待的连通性/正确性确认操作，且操作结果直接影响用户对该配置的信任决策。

| 位置 | 测试/验证语义 | 状态栏类型 |
|------|-------------|----------|
| MainWindow | 爬取全生命周期反馈 | 独立 `_show_status` QLabel |
| PreviewPanel | 异步页面渲染验证规则生效 | `_ProgressStatusBar` |
| RulesDialog | 远程源更新 / AI 生成 / 校验 | `_ProgressStatusBar` |
| AdvancedSettingsDialog | AI 连接测试 + 代理导入/测试（仅 tab 2/3 可见） | `_ProgressStatusBar(show_status_bar=False)` |
| ProxyProfileEditDialog | 端点 TCP 连通性探测 | `_ProgressStatusBar` |
| AIProfileEditDialog | 无 | ❌ |
| ProxyEndpointEditDialog | 无 | ❌ |
| ThemeDialog | 无 | ❌ |
| CompletionReportDialog | 无 | ❌ |

纯编辑/设置对话框不配备——idle 态灰色占位 bar + 永久"就绪"仅贡献视觉噪音。其异步操作的临时反馈走按钮内联（13.5）或子页面通信（13.6）。

### 13.4 内联字段验证 — 表单级实时校验

准入条件：单字段值合法性在提交前即可判定（名称非空、格式匹配、不重复）。错误紧贴问题字段。

生命周期：初始 `.hide()` → 验证失败 `.show()` → 用户修正或验证通过 `.hide()`。

```python
# 构造 — 初始隐藏
self._name_error = QLabel("")
self._name_error.setStyleSheet(
    f"color: {get_theme_manager().get('danger')}; font-size: {FONT_MD}px;")
self._name_error.hide()
gl.addLayout(_form_row(widget=self._name_error))

# 验证
def _validate(self) -> bool:
    if not self._name:
        self._name_error.setText("名称不能为空")
        self._name_error.show()
        return False
    self._name_error.hide()
    return True
```

**禁止**：用持久状态栏代替字段级错误（信息与字段分离，用户需自行定位问题源）。禁止用 QMessageBox 打断编辑流。

### 13.5 按钮内联反馈 — 操作进行中

准入条件：对话框无 `_ProgressStatusBar`，但个别按钮触发短暂异步操作（≤ 若干秒）。反馈直接承载于按钮自身。

```python
def do_fetch():
    btn.setEnabled(False)
    btn.setText("拉取中…")
    # ... 启动异步操作 ...
    def on_done():
        btn.setText("↻")       # 恢复原始文本
        btn.setEnabled(True)
```

操作结果通知通过 `status_callback`（13.6）上抛给父级状态栏。禁止在无状态栏对话框中添加仅为一个按钮服务的空闲态脉动条。

### 13.6 子页面→父级通信 — 统一 `status_callback`

子页面不依赖父级具体类型。父级通过 `status_callback` 参数注入通知函数，子页面仅调用 `(msg, level)` 签名。

```python
class _SourcePage(QWidget):
    def __init__(self, cfg, status_callback=None, parent=None):
        self._show_status = status_callback or (
            lambda msg, level="success": None)

    def _on_update(self):
        self._show_status("正在更新源...", "info")
        # ...
        self._show_status("已更新 3 个源", "success")
```

父级注入到 `_ProgressStatusBar`：

```python
page = _SourcePage(status_callback=self._psb.show_status)
self._psb.connect_page(page)   # 自动检测 busy_changed + status_message 并接线
```

**禁止** `status_message` Signal 用于子页面通信——Signal 将通知机制编译进子页面类型签名，破坏父级无关性。`status_callback` 是本项目唯一标准子→父通知通道。存量 `status_message` 用法逐步迁移，`connect_page` 的 `hasattr(page, "status_message")` 检测保留以兼容。

### 13.7 决策树

```
需要用户明确回应/确认？
├── 是（不可逆操作 / 致命错误 / 缺失输入）
│   └── QMessageBox（13.2 / §3）
└── 否
    └── 作用域？
        ├── 单个字段值非法
        │   └── 内联字段验证（13.4）
        ├── 窗口级操作（有状态栏）
        │   └── start_pulse → show_status("info") → stop_pulse → show_status("success"|"error")（13.3）
        ├── 窗口级操作（无状态栏）
        │   └── 按钮内联反馈（13.5）→ 结果通过 status_callback 上抛父级（13.6）
        ├── 按钮触发的短暂异步操作
        │   └── 按钮内联反馈（13.5）
        └── 子页面异步操作结果
            └── status_callback 上抛（13.6）

## 14. 脉动/进度条空闲态统一

所有脉动/渐变动画控件（`WorkerStatusBar`、`_PulseBar`）及代理健康条占位段，空闲态必须使用完全相同的渲染：

```python
# 空闲态 — 三条统一公式
path = QPainterPath()
path.addRoundedRect(0, 0, w, h, RADIUS_MD, RADIUS_MD)
painter.fillPath(path, QColor(theme.get("disabled")))
```

**强制要求**：
- QPainter `fillPath` + `addRoundedRect(RADIUS_MD)` — 圆角形状
- 颜色 `disabled` token — 通过 `QColor(theme.get("disabled"))` 获取
- `QPainter.RenderHint.Antialiasing` = `False` — 与活跃态一致
- **禁止** alpha 透明叠加（如 `setAlpha(76)`）—— 所有空闲态使用实色
- **禁止** QSS `background-color` 控制空闲态颜色 —— 必须走 QPainter 管线
- **禁止** `fillRect` 平角 —— 活跃态和空闲态统一圆角

WorkerStatusBar 状态简化为两态：`not self._working` → 空闲态（`disabled` 实色），`working` → 活跃态（`worker_grad_start` → `worker_grad_end` 渐变）。

`connect_session()` 禁止经过 `stop()`→`_on_stop()` 路径（会清空 `_session` 导致中间帧），直接停时器→赋 session→清 `_working`→启时器→`repaint()`。

## 15. 代理健康条

### 15.1 始终可见

代理健康条始终可见，不通过 `setVisible` 控制显示/隐藏。

- 无代理时：显示 1 个灰色占位段（`_PlaceholderSegment`），QPainter 渲染，与其他脉动条空闲态完全一致
- 有代理时：按代理数量均分为色块，每 3s 刷新健康色

### 15.2 段数实时同步

以下路径必须立即更新代理条段数：

| 路径 | 调用 | 备注 |
|------|------|------|
| 导入代理文件 | `set_source(proxies, None)` | 无健康数据，灰色段 |
| 加载含代理的配置 | `set_source(proxies, None)` | 同上 |
| 加载不含代理的配置 | `stop()` | 重置为占位段 |
| 爬取启动后 | `set_source(proxies, tracker)` | 健康追踪器接入，实时色 |
| 爬取结束 / 重置 | `stop()` | 重置为占位段 |

### 15.3 无健康数据时的渲染

当 `_health` 为 `None` 但 `_proxies` 非空时，`_refresh()` 以 `disabled` 色渲染所有色块：

```python
if not self._health:
    for seg in self._segments:
        seg.setStyleSheet(
            f"QFrame {{ background-color: {self._theme.get('disabled')}; "
            f"border: 1px solid {border}; border-radius: {RADIUS_MD}px; }}"
        )
    return
```
