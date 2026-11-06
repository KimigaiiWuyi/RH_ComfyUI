"""gemini_omni_mapper — GenerationRequest → Gemini Omni Interactions 请求/响应

input 段序与官方示例一致:媒体(图/视频)在前、文本提示词在后。
- 图片:request.images + ordered_content 的图片项,mime 按字节嗅探
- 视频:request.video_refs + ordered_content 的视频项(MediaRef),mime 用
  ref 已 sniff 的值;只有 url 没有 data 的 ref 发 ``uri`` 字段
- 文本:request.prompt + ordered_content 的文本段拼接

扁平字段与 ordered_content 双发是常态(同一段媒体两份副本),去重必须用
内容键(sha256 / url),``id()`` 对两份副本无效。
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from ..core.types import NodeOutput, media_ref_cache_key
from ..image_process import image_mime_from_bytes
from ...core.schema.types import MediaRef, ContentItemType
from ...core.schema.request import GenerationRequest

_VALID_RATIOS = {"16:9", "9:16"}
_VALID_RESOLUTIONS = {"360p", "720p", "1080p", "4k"}


def iter_image_bytes(request: GenerationRequest) -> list[bytes]:
    """扁平 images + OC 图项,按内容 sha256 去重(双发副本只发一份)。"""
    out: list[bytes] = []
    seen: set[str] = set()
    for img in request.images:
        if not img:
            continue
        # 与 media_ref_cache_key 的 data 分支同格式,扁平/OC 双发副本撞同一键
        key = f"sha256:{hashlib.sha256(img).hexdigest()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(img)
    for item in request.ordered_content:
        media = item.media
        if item.type != ContentItemType.IMAGE or media is None or not media.data:
            continue
        key = media_ref_cache_key(media)
        if key in seen:
            continue
        seen.add(key)
        out.append(media.data)
    return out


def iter_video_refs(request: GenerationRequest) -> list[MediaRef]:
    """扁平 video_refs + OC 视频项,按 media_ref_cache_key 去重。"""
    out: list[MediaRef] = []
    seen: set[str] = set()
    for ref in request.video_refs:
        key = media_ref_cache_key(ref)
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    for item in request.ordered_content:
        media = item.media
        if item.type != ContentItemType.VIDEO or media is None:
            continue
        key = media_ref_cache_key(media)
        if key in seen:
            continue
        seen.add(key)
        out.append(media)
    return out


def _video_content(ref: MediaRef) -> dict[str, Any] | None:
    if ref.data:
        return {
            "type": "video",
            "data": base64.b64encode(ref.data).decode(),
            "mime_type": ref.mime_type or "video/mp4",
        }
    if ref.url:
        return {"type": "video", "uri": ref.url}
    return None


async def gemini_omni_mapper(request: GenerationRequest, api: Any) -> NodeOutput:
    """组装 Interactions 请求并转成 NodeOutput。api 为 GeminiOmniAPI 实例。"""
    contents: list[dict[str, Any]] = []
    for img in iter_image_bytes(request):
        contents.append(
            {"type": "image", "data": base64.b64encode(img).decode(), "mime_type": image_mime_from_bytes(img)}
        )
    for ref in iter_video_refs(request):
        part = _video_content(ref)
        if part is not None:
            contents.append(part)

    text_parts: list[str] = []
    if (request.prompt or "").strip():
        text_parts.append(request.prompt.strip())
    for item in request.ordered_content:
        text = item.text
        if item.type == ContentItemType.TEXT and isinstance(text, str) and text.strip():
            t = text.strip()
            if t not in text_parts:
                text_parts.append(t)
    prompt_text = "\n".join(text_parts)
    if prompt_text:
        contents.append({"type": "text", "text": prompt_text})
    if not contents:
        raise ValueError("Gemini Omni 请求为空:需要提示词或参考媒体")

    ratio = str(request.ratio or request.params.get("ratio") or "16:9").strip()
    if ratio not in _VALID_RATIOS:
        ratio = "16:9"
    resolution = str(request.resolution or request.params.get("resolution") or "720p").strip().lower()
    if resolution not in _VALID_RESOLUTIONS:
        resolution = "720p"
    raw_duration = request.duration if request.duration is not None else request.params.get("duration")
    try:
        duration_s = int(float(raw_duration)) if raw_duration is not None else 10
    except (TypeError, ValueError):
        duration_s = 10
    duration_s = max(0, min(15, duration_s))

    model = str(request.params.get("model") or "").strip() or "gemini-omni-1.2-flash"
    data, usage = await api.create_video(
        model=model,
        contents=contents,
        aspect_ratio=ratio,
        resolution=resolution,
        duration_s=duration_s,
        prompt=prompt_text,
    )
    return NodeOutput(
        status="ok",
        output_type="video",
        data=data,
        mime_type="video/mp4",
        usage=usage,
        metadata={"model": model, "duration": duration_s, "resolution": resolution},
    )


__all__ = ["gemini_omni_mapper", "iter_image_bytes", "iter_video_refs"]
