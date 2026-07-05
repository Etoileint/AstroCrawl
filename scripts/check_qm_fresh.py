#!/usr/bin/env python3
"""pre-commit hook: 检查 .qm 是否比 .ts 更新。"""

from __future__ import annotations

import sys
from pathlib import Path

TS = Path("astrocrawl/gui/translations/astrocrawl_gui_zh_CN.ts")
QM = Path("astrocrawl/gui/translations/astrocrawl_gui_zh_CN.qm")

if TS.stat().st_mtime > QM.stat().st_mtime:
    print(
        "i18n: .ts 比 .qm 新，请运行: lrelease6 astrocrawl/gui/translations/astrocrawl_gui_zh_CN.ts -qm astrocrawl/gui/translations/astrocrawl_gui_zh_CN.qm"
    )
    sys.exit(1)
