"""AI 配置 — AIConfig + GenerationParams + _ResolvedParams（ADR-0006 两层合并 + 字段升级）。

ADR-0007: AIConfig.from_profile() 工厂方法。
ADR-0008: OutputConstraint + _ResolvedOutput — 结构化输出两层翻译。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrocrawl.ai._constraint import OutputConstraint
    from astrocrawl.ai._profile import AIProfile


@dataclass(frozen=True)
class GenerationParams:
    """单次生成的参数——全字段 Optional[None]，None 从 AIConfig 填充。

    presence_penalty / frequency_penalty 移除——通过 extra_body 透传。
    """

    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    seed: int | None = None
    stop: list[str] | None = None
    extra_body: dict | None = None
    output: OutputConstraint | None = None  # ADR-0008


@dataclass(frozen=True)
class AIConfig:
    """AI 客户端全局配置（ADR-0006 字段升级）。

    Attributes:
        api_key: API key
        provider: Provider 名（"openai" / "anthropic" / "google"）
        base_url: API endpoint 覆盖（空 = 各 Provider 默认端点）
        default_model: 默认模型名
        default_temperature: 默认温度（#4 新增）
        default_max_tokens: 默认最大 token 数（#4 新增）
        timeout: 请求超时 (秒)
        max_retries: SDK 内置 retry 次数（3→2，对齐 OpenAI SDK 默认值）
    """

    api_key: str = field(default="", repr=False)
    provider: str = "openai"
    base_url: str = ""
    default_model: str = "gpt-4o-mini"
    default_temperature: float = 0.7
    default_max_tokens: int = 4096
    timeout: float = 60.0
    max_retries: int = 2

    @classmethod
    def from_profile(cls, profile: AIProfile) -> AIConfig:
        """从 AIProfile 构造 AIConfig。

        timeout/max_retries 不暴露给用户，使用默认值。
        """
        return cls(
            api_key=profile.api_key,
            provider=profile.provider,
            base_url=profile.endpoint,
            default_model=profile.model,
            default_temperature=profile.temperature,
            default_max_tokens=profile.max_tokens,
        )


# ═══════════════════════════════════════════════════════════════════════
# _ResolvedOutput — 内部类型，对标 _ResolvedParams 两层翻译模式
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _ResolvedOutput:
    """_resolve_params() 产出——schema_model (type) → json_schema (dict)。"""

    format: str
    json_schema: dict | None  # model_json_schema() 推导，仅 json_schema 模式非空


# ═══════════════════════════════════════════════════════════════════════
# _ResolvedParams — 内部类型，None 字段已从 AIConfig 填充完毕
# ═══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _ResolvedParams:
    """_resolve_params() 产出——可选字段已填充完毕。仅内部使用。

    Provider 接收此类型，保证 model/temperature/max_tokens 非 None。
    top_p/seed/stop/extra_body/output 保持 Optional（AIConfig 无对应默认值）。
    """

    model: str
    temperature: float
    max_tokens: int
    top_p: float | None = None
    seed: int | None = None
    stop: list[str] | None = None
    extra_body: dict | None = None
    output: _ResolvedOutput | None = None  # ADR-0008


def _normalize_strict_schema(schema: dict) -> dict:
    """将 Pydantic model_json_schema() 输出标准化为 OpenAI strict mode 兼容格式。

    修复五类 Pydantic → JSON Schema 映射副作用：
    1. 所有 object 节点补齐 ``additionalProperties: false`` + ``required`` 数组
    2. 移除 ``pattern`` (regex) — strict 禁止
    3. 移除 ``maxProperties`` / ``minProperties`` — strict 禁止
    4. ``anyOf`` / ``oneOf`` 重写为 ``type: [..., "null"]`` — strict 禁止组合关键字
    5. ``additionalProperties: true`` 强制改为 ``false``

    ``$defs`` / ``$ref`` 保留（OpenAI 原生支持），递归进入规范化。
    ``additionalProperties`` 为 schema dict 时保留（map/dict 类型的合法表示）。
    循环引用安全（通过 ``id()`` 去重）。
    """
    import copy

    schema = copy.deepcopy(schema)
    _seen: set[int] = set()

    # ── Pydantic 产物中需清除的 strict 禁止 key ──
    _STRIP_KEYS = frozenset({"pattern", "maxProperties", "minProperties"})

    def _walk(node: dict) -> None:
        nid = id(node)
        if nid in _seen:
            return
        _seen.add(nid)

        # ── 清除禁止 key ──
        for fk in _STRIP_KEYS:
            node.pop(fk, None)

        # ── anyOf / oneOf → type union ──
        for ak in ("anyOf", "oneOf"):
            entries = node.pop(ak, None)
            if not isinstance(entries, list):
                continue
            types: list[str] = []
            extras: dict = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                t = entry.get("type")
                if t == "null":
                    types.append("null")
                elif t is not None:
                    if isinstance(t, str):
                        types.append(t)
                    for carryover in ("enum", "properties", "items", "description"):
                        if carryover in entry:
                            extras[carryover] = entry[carryover]
            if types:
                node["type"] = types if len(types) > 1 else types[0]
                for k, v in extras.items():
                    node.setdefault(k, v)
                # 若 type 含 "null" 且有 enum，将 null 加入 enum
                if "null" in types and "enum" in node:
                    enum_vals: list = node["enum"]
                    if None not in enum_vals:
                        enum_vals.append(None)

        # ── object 约束 ──
        t = node.get("type")
        is_obj = t == "object" or (isinstance(t, list) and "object" in t) or "properties" in node
        if is_obj:
            ap = node.get("additionalProperties", "ABSENT")
            if ap == "ABSENT":
                node["additionalProperties"] = False
            elif ap is True:
                node["additionalProperties"] = False
            # ap 为 dict (map 类型如 dict[str, X]) 时保留

            props = node.get("properties", {})
            if props and "required" not in node:
                node["required"] = list(props.keys())

            # 裸 object (无 properties 无 $ref, ap=false) → 只能输出 {}。
            # 检测已知结构并补齐 properties，防止模型被约束成空对象。
            if not props and "$ref" not in node:
                desc = node.get("description", "")
                if "from" in desc and "to" in desc:
                    node["properties"] = {
                        "from": {"type": "string", "description": "Original text to replace"},
                        "to": {"type": "string", "description": "Replacement text"},
                    }
                    node["required"] = list(node["properties"].keys())

        # ── 递归 ──
        items = node.get("items")
        if isinstance(items, dict):
            _walk(items)

        for pnode in node.get("properties", {}).values():
            if isinstance(pnode, dict):
                _walk(pnode)

        for dnode in node.get("$defs", {}).values():
            if isinstance(dnode, dict):
                _walk(dnode)

        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            def_name = ref[len("#/$defs/") :]
            def_node = schema.get("$defs", {}).get(def_name)
            if isinstance(def_node, dict):
                _walk(def_node)

    _walk(schema)
    return schema


def _resolve_params(
    params: GenerationParams | None,
    *,
    default_model: str = "gpt-4o-mini",
    default_temperature: float = 0.7,
    default_max_tokens: int = 4096,
) -> _ResolvedParams:
    """将 GenerationParams 的 None 字段从 AIConfig 默认值填充。

    temperature=0.0 不会被误判为 None（使用 ``is not None`` 检查）。
    #4: GenerationParams 字段全部 Optional[None]——is not None 检查正式生效。
    """
    p = params or GenerationParams()

    # ADR-0008: 翻译 output 约束
    resolved_output: _ResolvedOutput | None = None
    if p.output is not None:
        json_schema: dict | None = None
        if p.output.schema_model is not None:
            if not hasattr(p.output.schema_model, "model_json_schema"):
                raise ValueError("schema_model 必须是 Pydantic BaseModel 子类")
            json_schema = p.output.schema_model.model_json_schema()
            json_schema = _normalize_strict_schema(json_schema)
        elif p.output.format == "json_schema":
            raise ValueError("format='json_schema' 要求提供 schema_model")
        resolved_output = _ResolvedOutput(format=p.output.format, json_schema=json_schema)

    return _ResolvedParams(
        model=p.model if p.model is not None else default_model,
        temperature=p.temperature if p.temperature is not None else default_temperature,
        max_tokens=p.max_tokens if p.max_tokens is not None else default_max_tokens,
        top_p=p.top_p,
        seed=p.seed,
        stop=list(p.stop) if p.stop else None,
        extra_body=dict(p.extra_body) if p.extra_body else None,
        output=resolved_output,
    )
