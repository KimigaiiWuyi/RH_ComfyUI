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
    """通用校验:required / 枚举值 / 数值范围 / 列表基数"""
    for key, spec in schema.items():
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

    if request.audio_refs and "audio_refs" not in schema and "reference_audio" not in schema:
        return False
    if request.video_refs and "video_refs" not in schema:
        return False
    return True


__all__ = ["validate_against_schema", "schema_supports_request"]
