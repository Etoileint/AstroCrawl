"""消费者注册 — 代理模块作为资源提供方自行声明消费者清单。

对标 Django settings / Celery task routes 的命名约定模式。
新增消费者 = 加一行字典条目，无需改 UI 代码。

GUI _RouteSettingsPage 遍历此字典渲染 consumer→profile 分配表。
"""

from __future__ import annotations

PROXY_CONSUMERS = {
    "preview": "Rule Preview",
    "ai": "AI Calls",
    "source": "Rule Source",
}
