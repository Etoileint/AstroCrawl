"""健康报告与健康检查协议。

对标 Spring Actuator Health + K8s probe：
- UP: 正常运行
- DEGRADED: 部分不健康但核心功能仍在（对标 K8s readiness）
- DOWN: 核心功能不可用（对标 K8s liveness）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True)
class Health:
    """统一健康报告。对标 Spring Actuator Health + K8s probe 结果。"""

    status: Literal["UP", "DEGRADED", "DOWN"]
    message: str = ""
    details: dict = field(default_factory=dict)

    @staticmethod
    def aggregate(components: dict[str, "Health"]) -> "Health":
        """聚合多个组件的健康状态。"""
        if not components:
            return Health("UP")
        down = [k for k, v in components.items() if v.status == "DOWN"]
        degraded = [k for k, v in components.items() if v.status == "DEGRADED"]
        if down:
            return Health("DOWN", f"{len(down)} component(s) DOWN: {', '.join(down[:3])}", {"components": components})
        if degraded:
            return Health("DEGRADED", f"{len(degraded)} component(s) DEGRADED", {"components": components})
        return Health("UP", "All components healthy", {"components": components})


class HealthChecked(Protocol):
    """被动健康报告——快速、无副作用。对标 Spring HealthIndicator。"""

    def get_health(self) -> Health: ...


def health_to_report(health: Health) -> dict:
    """将 Health 格式化为报告用的 JSON 兼容 dict。

    单向树变换：遍历 details["components"] 中的子 Health 对象，
    展开为 {"status": ..., **details} 格式。
    """
    raw_components = health.details.get("components", {})
    components = {}
    for name, h in raw_components.items():
        if isinstance(h, Health):
            components[name] = {"status": h.status, **h.details}
        elif isinstance(h, dict):
            components[name] = h
        else:
            components[name] = {"status": "UNKNOWN", "message": str(h)}
    return {"status": health.status, "components": components}
