"""媒体引用代号 — 从 ordered_content 重建 prompt 中的结构化参考标记

背景
----
调用方可能提交图文交错的 ``ordered_content``,而部分模型只接受扁平
``prompt + images``。若剥掉媒体占位只剩空白文案,会得到无主语的残缺 prompt。

约定代号(有结构,便于模型与下游改写识别):
  ``[参考图片N]`` / ``[参考视频N]`` / ``[参考音频N]``
与扁平 ``images[N-1]`` / ``video_refs`` / ``audio_refs`` 下标对齐。

本模块在引擎入口层统一兜底:
1. 有 ``ordered_content`` 媒体项时,按书写顺序重建带结构化代号的 prompt;
2. 扁平 ``images`` 为空时,从 OC 抽出图片 bytes,供只读 images 的通道使用。

同 media 多次出现复用同一序号(按 data 身份 / url 去重)。
"""

from __future__ import annotations

from typing import Any, Optional

from .types import MediaRef, MediaKind, ContentItem, ContentItemType
from .request import GenerationRequest


def _media_identity(ref: MediaRef) -> str:
    """去重键:优先 bytes id,再 url,再对象 id。"""
    if ref.data is not None:
        return f"bytes:{id(ref.data)}:{len(ref.data)}"
    if ref.url:
        return f"url:{ref.url}"
    return f"obj:{id(ref)}"


def _kind_of(item: ContentItem) -> Optional[MediaKind]:
    if item.media is not None:
        try:
            raw = item.media.kind.value if hasattr(item.media.kind, "value") else item.media.kind
            return MediaKind(raw)
        except Exception:  # noqa: BLE001
            pass
    if item.type == ContentItemType.IMAGE:
        return MediaKind.IMAGE
    if item.type == ContentItemType.VIDEO:
        return MediaKind.VIDEO
    if item.type == ContentItemType.AUDIO:
        return MediaKind.AUDIO
    return None


def labeled_prompt_from_ordered_content(items: list[ContentItem]) -> str:
    """按 ordered_content 书写顺序拼出带 ``[参考图片N]`` 等结构化代号的 prompt。

    文本段原样拼接;媒体项插入对应代号(同 media 去重复用序号)。
    无任何有效段时返回空串。
    """
    if not items:
        return ""

    parts: list[str] = []
    image_nums: dict[str, int] = {}
    video_nums: dict[str, int] = {}
    audio_nums: dict[str, int] = {}

    for item in items:
        if item.type == ContentItemType.TEXT:
            if item.text:
                parts.append(item.text)
            continue
        if item.media is None:
            continue
        kind = _kind_of(item)
        if kind is None:
            continue
        key = _media_identity(item.media)
        if kind == MediaKind.IMAGE:
            n = image_nums.get(key)
            if n is None:
                n = len(image_nums) + 1
                image_nums[key] = n
            parts.append(f"[参考图片{n}]")
        elif kind == MediaKind.VIDEO:
            n = video_nums.get(key)
            if n is None:
                n = len(video_nums) + 1
                video_nums[key] = n
            parts.append(f"[参考视频{n}]")
        elif kind == MediaKind.AUDIO:
            n = audio_nums.get(key)
            if n is None:
                n = len(audio_nums) + 1
                audio_nums[key] = n
            parts.append(f"[参考音频{n}]")

    return "".join(parts)


def flatten_image_bytes_from_ordered_content(items: list[ContentItem]) -> list[bytes]:
    """从 ordered_content 按出现顺序抽出图片 bytes(去重),供扁平 images 通道。"""
    out: list[bytes] = []
    seen: set[str] = set()
    for item in items:
        if item.media is None:
            continue
        kind = _kind_of(item)
        if kind != MediaKind.IMAGE or not item.media.data:
            continue
        key = _media_identity(item.media)
        if key in seen:
            continue
        seen.add(key)
        out.append(item.media.data)
    return out


def ensure_media_ref_labels(request: GenerationRequest) -> GenerationRequest:
    """就地补全 request.prompt 中的媒体代号,并在需要时回填 images。

    调用方:``ImageGenerationBase.normalize`` / ``VideoGenerationBase.normalize``。
    对无 ordered_content 媒体项的请求原样返回(纯扁平路径由调用方
    在提交前把 [@] 换成 图片N,见前端 stripMentions)。
    """
    oc = list(request.ordered_content or [])
    if not oc or not any(item.media is not None for item in oc):
        return request

    labeled = labeled_prompt_from_ordered_content(oc)
    if labeled.strip():
        # OC 是书写顺序的权威来源:banana/gpt-image/happyhorse 等只读 prompt 时
        # 必须能在文本里看到 [参考图片N];Seedance content[] 仍走 ordered_segments 注入。
        request.prompt = labeled

    # 只读 images 的通道(banana / gpt-image / seedream…):OC 有图而扁平 images 空时回填
    if not request.images:
        flat = flatten_image_bytes_from_ordered_content(oc)
        if flat:
            request.images = flat

    return request


def labeled_prompt_from_oc_dicts(items: list[Any]) -> str:
    """轻量版:接受 dict / duck-type 对象列表(字段 type/text/url/media)。

    供在无 ``ContentItem`` 的场景复用同一套编号语义。
    """
    if not items:
        return ""
    parts: list[str] = []
    image_nums: dict[str, int] = {}
    video_nums: dict[str, int] = {}
    audio_nums: dict[str, int] = {}

    for it in items:
        if isinstance(it, dict):
            t = str(it.get("type") or "")
            text = it.get("text")
            url = str(it.get("url") or "")
            media = it.get("media")
            if isinstance(media, dict) and not url:
                url = str(media.get("url") or "")
            filename = str(media.get("filename") or "") if isinstance(media, dict) else ""
        else:
            t = str(getattr(it, "type", "") or "")
            text = getattr(it, "text", None)
            url = str(getattr(it, "url", "") or "")
            media = getattr(it, "media", None)
            filename = ""
            if media is not None:
                url = url or str(getattr(media, "url", "") or "")
                filename = str(getattr(media, "filename", "") or "")

        if t == "text":
            if text:
                parts.append(str(text))
            continue
        key = url or filename or f"anon:{id(it)}"
        if t in ("image_url", "image"):
            n = image_nums.get(key)
            if n is None:
                n = len(image_nums) + 1
                image_nums[key] = n
            parts.append(f"[参考图片{n}]")
        elif t in ("video_url", "video"):
            n = video_nums.get(key)
            if n is None:
                n = len(video_nums) + 1
                video_nums[key] = n
            parts.append(f"[参考视频{n}]")
        elif t in ("audio_url", "audio"):
            n = audio_nums.get(key)
            if n is None:
                n = len(audio_nums) + 1
                audio_nums[key] = n
            parts.append(f"[参考音频{n}]")

    return "".join(parts)


__all__ = [
    "labeled_prompt_from_ordered_content",
    "flatten_image_bytes_from_ordered_content",
    "ensure_media_ref_labels",
    "labeled_prompt_from_oc_dicts",
]
