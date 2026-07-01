"""AI 输出约束 — Provider-agnostic 结构化输出声明。

对标 Instructor/LangChain 的 response_model 模式：
用户传 Pydantic BaseModel 子类，框架内部调 model_json_schema() 推导 JSON Schema。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class OutputConstraint:
    """Provider-agnostic 输出约束。output=None 表示无约束（默认）。

    使用示例:
        OutputConstraint(format="json_object")
        OutputConstraint(format="json_schema", schema_model=RuleSchema)
    """

    format: Literal["json_object", "json_schema"]
    schema_model: type | None = None  # Pydantic BaseModel subclass
