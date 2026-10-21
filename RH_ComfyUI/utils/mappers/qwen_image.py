"""千问图像请求体。同步 messages 协议,图片在文字前面。

2.0 不传改写模式和思考开关。2.1 不传反向提示词。
ratio 为 auto、空或未传时省略 size。
"""

from __future__ import annotations

from typing import TypedDict
from dataclasses import dataclass

from gsuid_core.logger import logger

from ..offload import data_url_async
from ..core.types import NodeOutput
from ..core.request import GenerationRequest
from ..image_process import image_mime_from_bytes
from ...core.media_host import MediaPublishError, materialize
from ...core.base.errors import ValidationError
from .qwen_image_catalog import (
    SIZE_1K,
    SIZE_2K,
    SEED_MAX,
    NEGATIVE_LIMIT,
    MAX_INPUT_BYTES,
    MAX_OUTPUT_IMAGES,
    QwenImageSpec,
)
from ..backends.dashscope.image_api import QwenImageResult, DashScopeImageAPI


class QwenContentImage(TypedDict):
    image: str


class QwenContentText(TypedDict):
    text: str


class QwenMessage(TypedDict):
    role: str
    content: list[QwenContentImage | QwenContentText]


class QwenInput(TypedDict):
    messages: list[QwenMessage]


class QwenParameters(TypedDict, total=False):
    negative_prompt: str
    prompt_extend: bool
    prompt_extend_mode: str
    enable_thinking: bool
    n: int
    size: str
    seed: int
    watermark: bool


class QwenImageBody(TypedDict):
    model: str
    input: QwenInput
    parameters: QwenParameters


@dataclass(frozen=True)
class QwenImageCall:
    model: str
    text: str
    images: tuple[bytes, ...]
    n: int
    size: str | None
    prompt_extend: bool
    prompt_extend_mode: str | None
    enable_thinking: bool | None
    negative_prompt: str | None
    seed: int | None
    watermark: bool


def resolve_qwen_image_size(ratio: str | None, image_size: str | None) -> str | None:
    """具体比例才发 宽*高。未传 image_size 按 1K,和目录默认一致。"""
    if ratio is None or ratio == "" or ratio == "auto":
        return None
    use_2k = isinstance(image_size, str) and image_size.strip().upper() == "2K"
    table = SIZE_2K if use_2k else SIZE_1K
    if ratio not in table:
        return None
    return table[ratio]


def estimate_output_count(raw: object) -> int:
    """估算路径:缺省或无法识别当 1。不截断上限,提交时再拒绝。"""
    if raw is None or isinstance(raw, bool):
        return 1
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        return int(raw.strip())
    return 1


def _param(request: GenerationRequest, key: str) -> object:
    if key not in request.params:
        return None
    value: object = request.params[key]
    return value


def _flag(raw: object, default: bool, label: str, key: str) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    raise ValidationError(f"{label} 的 {key} 必须是布尔值")


def _mode(raw: object, label: str) -> str:
    if raw is None:
        return "direct"
    if isinstance(raw, str) and raw in ("direct", "agent"):
        return raw
    raise ValidationError(f"{label} 的改写方式只支持 direct 或 agent")


def _count(raw: object, label: str) -> int:
    if raw is None:
        return 1
    if isinstance(raw, bool):
        raise ValidationError(f"{label} 的张数必须是整数")
    value: int
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        value = int(raw.strip())
    else:
        raise ValidationError(f"{label} 的张数必须是整数")
    if value < 1 or value > MAX_OUTPUT_IMAGES:
        raise ValidationError(f"{label} 的张数须在 1~{MAX_OUTPUT_IMAGES},当前 {value}")
    return value


def _images(request: GenerationRequest, spec: QwenImageSpec) -> tuple[bytes, ...]:
    found: list[bytes] = []
    for blob in request.images:
        if not isinstance(blob, (bytes, bytearray)) or not blob:
            raise ValidationError(f"{spec.display_name} 的参考图是空的")
        raw = bytes(blob)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValidationError(f"{spec.display_name} 的单张参考图不能超过 10MB")
        found.append(raw)
    if len(found) > spec.max_images:
        raise ValidationError(f"{spec.display_name} 最多 {spec.max_images} 张参考图,当前 {len(found)} 张")
    return tuple(found)


def call_from_request(spec: QwenImageSpec, request: GenerationRequest) -> QwenImageCall:
    """纯本地组装。不访问网络。"""
    label = spec.display_name
    for item in request.ordered_content:
        if item.media is not None:
            raise ValidationError(f"{label} 请把参考图放在 images")
    text = request.prompt
    if not isinstance(text, str) or not text.strip():
        raise ValidationError(f"{label} 必须填写提示词")
    images = _images(request, spec)
    negative = request.negative_prompt.strip() if isinstance(request.negative_prompt, str) else ""
    if negative and not spec.allow_negative:
        raise ValidationError(f"{label} 不支持反向提示词")
    if len(negative) > NEGATIVE_LIMIT:
        raise ValidationError(f"{label} 的反向提示词最长 {NEGATIVE_LIMIT} 个字符")
    if not spec.allow_extend_mode and _param(request, "prompt_extend_mode") is not None:
        raise ValidationError(f"{label} 不支持改写方式")
    if not spec.allow_extend_mode and _param(request, "enable_thinking") is not None:
        raise ValidationError(f"{label} 不支持思考开关")
    extend = _flag(_param(request, "prompt_extend"), True, label, "prompt_extend")
    mode = _mode(_param(request, "prompt_extend_mode"), label) if spec.allow_extend_mode else "direct"
    if mode == "agent" and images and spec.allow_extend_mode:
        raise ValidationError(f"{label} 有参考图时不能使用 agent 改写")
    thinking = _flag(_param(request, "enable_thinking"), True, label, "enable_thinking")
    seed = request.seed
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or seed < 0 or seed > SEED_MAX):
        raise ValidationError(f"{label} 的种子须在 0~{SEED_MAX}")
    watermark = request.watermark
    if not isinstance(watermark, bool):
        raise ValidationError(f"{label} 的水印必须是布尔值")
    size_raw = _param(request, "image_size")
    image_size = size_raw if isinstance(size_raw, str) else None
    send_mode = mode if spec.allow_extend_mode and extend else None
    send_thinking = thinking if spec.allow_extend_mode and extend else None
    return QwenImageCall(
        model=spec.name,
        text=text,
        images=images,
        n=_count(_param(request, "n"), label),
        size=resolve_qwen_image_size(request.ratio, image_size),
        prompt_extend=extend,
        prompt_extend_mode=send_mode,
        enable_thinking=send_thinking,
        negative_prompt=negative or None,
        seed=seed,
        watermark=watermark,
    )


def build_qwen_image_body(call: QwenImageCall, urls: list[str]) -> QwenImageBody:
    content: list[QwenContentImage | QwenContentText] = []
    for url in urls:
        content.append({"image": url})
    content.append({"text": call.text})
    parameters: QwenParameters = {
        "n": call.n,
        "prompt_extend": call.prompt_extend,
        "watermark": call.watermark,
    }
    if call.size is not None:
        parameters["size"] = call.size
    if call.negative_prompt is not None:
        parameters["negative_prompt"] = call.negative_prompt
    if call.prompt_extend_mode is not None:
        parameters["prompt_extend_mode"] = call.prompt_extend_mode
    if call.enable_thinking is not None:
        parameters["enable_thinking"] = call.enable_thinking
    if call.seed is not None:
        parameters["seed"] = call.seed
    return {
        "model": call.model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": parameters,
    }


async def _image_urls(images: tuple[bytes, ...]) -> list[str]:
    if not images:
        return []
    urls: list[str | None] = []
    publish_failed = False
    for blob in images:
        mime = image_mime_from_bytes(blob)
        try:
            urls.append(await materialize(blob, mime))
        except MediaPublishError as exc:
            publish_failed = True
            logger.warning(f"[qwen-image] 参考图发布失败({type(exc).__name__}),改为 data URL")
            urls = []
            break
    ready = [item for item in urls if isinstance(item, str) and item]
    # 没注册发布器时全是 None,这是正常回落,不记警告。
    if not publish_failed and ready and len(ready) != len(images):
        logger.warning("[qwen-image] 参考图没有完整公网地址,改为 data URL")
    if not publish_failed and len(ready) == len(images):
        return ready
    encoded: list[str] = []
    for blob in images:
        encoded.append(await data_url_async(blob, image_mime_from_bytes(blob)))
    return encoded


def _node_output(result: QwenImageResult) -> NodeOutput:
    out = NodeOutput(
        status="ok",
        output_type="image",
        data=result.images[0],
        mime_type=result.mime,
    )
    index = 2
    for blob in result.images[1:]:
        out.outputs[f"image_{index}"] = blob
        index += 1
    for key, value in result.usage.items():
        out.usage[key] = value
    return out


async def qwen_image_mapper(
    request: GenerationRequest,
    api: DashScopeImageAPI,
    *,
    spec: QwenImageSpec,
) -> NodeOutput:
    call = call_from_request(spec, request)
    body = build_qwen_image_body(call, await _image_urls(call.images))
    result = await api.generate(body, prompt=call.text)
    return _node_output(result)


__all__ = [
    "QwenImageBody",
    "QwenImageCall",
    "build_qwen_image_body",
    "call_from_request",
    "estimate_output_count",
    "qwen_image_mapper",
    "resolve_qwen_image_size",
]
