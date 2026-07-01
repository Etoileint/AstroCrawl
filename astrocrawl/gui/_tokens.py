"""GUI 布局常量 — 间距 / 圆角 / 字体 / 高度。

纯常量模块，不依赖 astrocrawl.gui 内任何模块，可被任意 GUI 文件安全导入。
"""

from __future__ import annotations

# 间距阶梯
SPACE_XS = 4  # 紧贴（标题栏按钮内间距、表单垂直间距、色块-标签间距）
SPACE_SM = 6  # 组内（config 组内间距、表单行间距）
SPACE_MD = 8  # 默认（根布局间距、子页面顶部边距、对话框垂直间距）
SPACE_LG = 12  # 窗口边距、表单列间距

# 圆角
RADIUS_SM = 3  # 小圆角
RADIUS_MD = 4  # 默认圆角（等价旧 CORNER_RADIUS，值不变）

# 字体阶梯
FONT_SM = 11  # 辅助信息（outcome 统计）
FONT_MD = 12  # 正文（URL 状态、ChatML 代码、进度条）

# 标准高度
BAR_HEIGHT = 24  # 状态条统一高度（WorkerStatusBar / TitleBar 主题按钮 / ProxyHealthBar 段）

# 动画
PULSE_ANIM_MS = 33  # 脉动条动画帧间隔（~30 FPS，WorkerStatusBar / _PulseBar 共用）

# 规则预览 — 字段高亮颜色调色板（10 色循环分配）
PREVIEW_FIELD_COLORS = (
    "#FF6B6B",
    "#4ECDC4",
    "#45B7D1",
    "#96CEB4",
    "#FFEAA7",
    "#DDA0DD",
    "#F0B27A",
    "#A29BFE",
    "#FF8A80",
    "#A8D8EA",
)
