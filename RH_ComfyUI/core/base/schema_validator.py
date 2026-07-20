"""基于 input_schema(dict[str, PortSpec]) 的通用请求校验器"""

from __future__ import annotations

from .errors import ValidationError
from ..schema.types import PortSpec, PortType
from ..schema.request import GenerationRequest


def _request_value(request: GenerationRequest, key: str) -> object:
    """schema key 与 request 字段同名约定;模型私有参数走 params 旁路"""
    if hasattr(request, key):
        return getattr(request, key)
    return request.params.get(key)


def validate_against_schema(
    request: GenerationRequest,
    schema: dict[str, PortSpec],
    *,
    model_name: str,
) -> None:
    """通用校验:required / 枚举值 / 数值范围 / 列表基数

    端口名与 ``GenerationRequest`` 字段名约定:
    - ``audio_payload`` 端口对应 ``request.audio_payload`` (bytes);
    - ``audio_refs`` / ``reference_audio`` 端口对应 ``request.audio_refs`` (MediaRef 列表),
      任一即可视为 ASR 音频输入已满足 —— 这样模型既支持直接字节透传,
      也支持引用式的音频(后者用于 Adapter 链上批量 / 多参考场景)。
    """
    for key, spec in schema.items():
        # audio_payload 端口:audio_refs 携带非空 bytes 时也视为满足
        if key == "audio_payload":
            value = request.audio_payload
            has_alt = bool(request.audio_refs) and any(
                bool(getattr(ref, "data", None)) for ref in request.audio_refs
            )
            is_empty = not value and not has_alt
            if spec.required and is_empty:
                raise ValidationError(
                    f"[{model_name}] 缺少必填参数: {key}({spec.description})"
                )
            if is_empty:
                continue
            value = value or (request.audio_refs[0].data if has_alt else None)
        else:
            value = _request_value(request, key)
            is_empty = value is None or value == "" or value == [] or value == {}
            if spec.required and is_empty:
                raise ValidationError(f"[{model_name}] 缺少必填参数: {key}({spec.description})")
            if is_empty:
                continue

        if spec.type == PortType.ENUM and spec.values is not None:
            if value not in spec.values:
                raise ValidationError(f"[{model_name}] 参数 {key}={value!r} 不合法,可选: {spec.values}")
        if spec.type in (PortType.INTEGER, PortType.NUMBER) and isinstance(value, (int, float)):
            if spec.minimum is not None and value < spec.minimum:
                raise ValidationError(f"[{model_name}] {key} 不能小于 {spec.minimum}")
            if spec.maximum is not None and value > spec.maximum:
                raise ValidationError(f"[{model_name}] {key} 不能大于 {spec.maximum}")
        if spec.type == PortType.LIST and isinstance(value, list):
            if spec.min_items is not None and len(value) < spec.min_items:
                raise ValidationError(f"[{model_name}] {key} 至少需要 {spec.min_items} 项")
            if spec.max_items is not None and len(value) > spec.max_items:
                raise ValidationError(f"[{model_name}] {key} 最多 {spec.max_items} 项,当前 {len(value)} 项")


def schema_supports_request(request: GenerationRequest, schema: dict[str, PortSpec]) -> bool:
    """路由用输入档案匹配(平移旧 router._node_supports_request 语义)

    - 请求带图但 schema 无 images 端口 → False
    - 图片数超出 images 端口 max_items / 不足 min_items → False
    - 请求带音/视频参考但 schema 无对应端口 → False
    - schema 要求必须有图但请求没图 → False
    """
    if not schema:
        return True

    n_img = len(request.images)
    img_port = schema.get("images") or schema.get("image") or schema.get("image_url")
    if n_img > 0:
        if img_port is None:
            return False
        if img_port.max_items is not None and n_img > img_port.max_items:
            return False
        min_items = img_port.min_items if img_port.min_items is not None else (1 if img_port.required else 0)
        if n_img < min_items:
            return False
    elif img_port is not None and (img_port.required or (img_port.min_items or 0) >= 1):
        return False

    if request.video_refs and "video_refs" not in schema:
        return False

    # ASR / 音频引用:schema 里有 audio_payload / audio_refs / reference_audio 任一即可
    # ASR 必备音频输入;请求带音频但 schema 一个端口都没有 → 不支持
    audio_keys = ("audio_payload", "audio_refs", "reference_audio")
    has_audio_input = bool(request.audio_payload) or bool(request.audio_refs)
    if has_audio_input and not any(k in schema for k in audio_keys):
        return False
    # 反向:schema 声明必须 audio_payload 且请求完全没音频 → 不支持
    if not has_audio_input and schema.get("audio_payload") is not None:
        return False

    return True


__all__ = ["validate_against_schema", "schema_supports_request"]
