"""DashScope 万相 3.0 Provider

复用 HappyHorseProvider 的 HTTP / 轮询 / cancel / wire;只覆盖
形态校验与 media 渲染。供应商 model 固定 ``wan3.0-video``。
"""

from __future__ import annotations

from typing import Any, Optional

from .classify import (
    VENDOR_MODEL,
    file_url_of,
    link_url_of,
    to_api_resolution,
    rewrite_prompt_for_wan30,
)
from ..seedance.spec import MediaRole, VideoGenSpec, VideoTaskShape
from ..happyhorse.provider import HappyHorseProvider, HappyHorseProviderError


class Wan30ProviderError(HappyHorseProviderError):
    """万相 3.0 供应商错误。"""


class Wan30Provider(HappyHorseProvider):
    """DashScope 万相 3.0,凭证与 HappyHorse 共用。"""

    name = "dashscope"
    VENDOR_MODEL = VENDOR_MODEL

    supported_shapes = {
        VideoTaskShape.TEXT2VIDEO,
        VideoTaskShape.IMAGE2VIDEO,
        VideoTaskShape.FIRST_LAST_FRAME,
        VideoTaskShape.MULTIMODAL,
    }
    supported_resolutions = {"480p", "720p", "1080p"}
    supported_ratios = {"adaptive", "16:9", "4:3", "1:1", "3:4", "9:16"}
    min_duration = 2
    max_duration = 30
    max_images = 10
    max_videos = 5
    max_audios = 5
    poll_interval = 15.0

    def can_handle_spec(self, spec: VideoGenSpec) -> bool:
        if self.supported_shapes and spec.shape not in self.supported_shapes:
            return False
        res = (spec.resolution or "").lower()
        if self.supported_resolutions and res and res not in self.supported_resolutions:
            return False
        if self.supported_ratios and spec.ratio is not None and spec.ratio not in self.supported_ratios:
            return False
        if spec.duration == -1:
            return True
        if self.min_duration and spec.duration and spec.duration < self.min_duration:
            return False
        if self.max_duration and spec.duration and spec.duration > self.max_duration:
            return False
        return True

    def validate_spec(self, spec: VideoGenSpec) -> None:
        if self.supported_shapes and spec.shape not in self.supported_shapes:
            raise Wan30ProviderError(
                f"万相 3.0 不支持任务形态 {spec.shape.value}",
                code="UNSUPPORTED_SHAPE",
                provider=self.name,
            )
        n_img = len(spec.images())
        n_vid = len(spec.videos())
        n_aud = len(spec.audios())
        file_url = file_url_of(spec.params)
        link_url = link_url_of(spec.params)
        if file_url and link_url:
            raise Wan30ProviderError(
                "参考文件与网页链接不能同时传入",
                code="FILE_LINK_CONFLICT",
                provider=self.name,
                user_message="万相 3.0 的参考文件与网页链接不能同时使用,请只保留一项。",
            )
        if n_img > self.max_images:
            raise Wan30ProviderError(
                f"万相 3.0 最多 {self.max_images} 张参考图,当前 {n_img}",
                code="MEDIA_OVERFLOW",
                provider=self.name,
            )
        if n_vid > self.max_videos:
            raise Wan30ProviderError(
                f"万相 3.0 最多 {self.max_videos} 段参考视频,当前 {n_vid}",
                code="MEDIA_OVERFLOW",
                provider=self.name,
            )
        if n_aud > self.max_audios:
            raise Wan30ProviderError(
                f"万相 3.0 最多 {self.max_audios} 段参考音频,当前 {n_aud}",
                code="MEDIA_OVERFLOW",
                provider=self.name,
            )

        frame_like = spec.shape in (VideoTaskShape.IMAGE2VIDEO, VideoTaskShape.FIRST_LAST_FRAME)
        if frame_like and (n_vid or n_aud or file_url or link_url):
            raise Wan30ProviderError(
                "首帧/首尾帧不能与参考视频、音频、文件或网页混用",
                code="MODE_CONFLICT",
                provider=self.name,
                user_message="万相 3.0 的首帧/首尾帧不能同时传参考视频、音频、文件或网页,请改用多参考或移除这些素材。",
            )

        if spec.duration != -1:
            if spec.duration and spec.duration < self.min_duration:
                raise Wan30ProviderError(
                    f"时长须为 {self.min_duration}~{self.max_duration} 秒或 -1,当前 {spec.duration}",
                    code="INVALID_DURATION",
                    provider=self.name,
                )
            if spec.duration and spec.duration > self.max_duration:
                raise Wan30ProviderError(
                    f"时长须为 {self.min_duration}~{self.max_duration} 秒或 -1,当前 {spec.duration}",
                    code="INVALID_DURATION",
                    provider=self.name,
                )

        prompt = (spec.prompt or "").strip()
        has_media = bool(n_img or n_vid or n_aud or file_url or link_url)
        if spec.shape == VideoTaskShape.TEXT2VIDEO:
            if not prompt:
                raise Wan30ProviderError(
                    "文生视频必须提供提示词",
                    code="MISSING_PROMPT",
                    provider=self.name,
                    user_message="文生视频需要填写提示词。",
                )
        elif spec.shape == VideoTaskShape.IMAGE2VIDEO:
            if n_img < 1:
                raise Wan30ProviderError(
                    "图生视频需要至少 1 张首帧图",
                    code="MISSING_IMAGE",
                    provider=self.name,
                    user_message="图生视频需要上传 1 张首帧图片。",
                )
        elif spec.shape == VideoTaskShape.FIRST_LAST_FRAME:
            if n_img < 1:
                raise Wan30ProviderError(
                    "首尾帧需要至少 1 张图",
                    code="MISSING_IMAGE",
                    provider=self.name,
                    user_message="首尾帧模式至少需要 1 张图片。",
                )
        elif spec.shape == VideoTaskShape.MULTIMODAL:
            if not has_media:
                raise Wan30ProviderError(
                    "全能参考需要至少 1 个图片/视频/音频/文件/网页素材",
                    code="MISSING_MEDIA",
                    provider=self.name,
                    user_message="全能参考模式需要至少 1 个参考素材。",
                )
        if not prompt and not has_media:
            raise Wan30ProviderError(
                "需要提示词或至少 1 个参考素材",
                code="MISSING_INPUT",
                provider=self.name,
                user_message="请填写提示词,或上传图片/视频/音频/文件/网页。",
            )

    async def render_create(
        self,
        spec: VideoGenSpec,
        *,
        model: Optional[str],
    ) -> tuple[str, str, dict[str, str], dict[str, Any]]:
        model_id = (model or "").strip() or self.VENDOR_MODEL
        media_items = await self._build_media(spec)
        prompt = rewrite_prompt_for_wan30((spec.prompt or "").strip())

        input_body: dict[str, Any] = {}
        if prompt:
            input_body["prompt"] = prompt
        if media_items:
            input_body["media"] = media_items

        parameters: dict[str, Any] = {}
        api_res = to_api_resolution(spec.resolution) or "1080P"
        parameters["resolution"] = api_res
        if spec.ratio:
            parameters["ratio"] = spec.ratio
        else:
            parameters["ratio"] = "adaptive"
        if spec.duration == -1:
            parameters["duration"] = -1
        elif spec.duration:
            parameters["duration"] = int(spec.duration)
        parameters["audio"] = bool(spec.generate_audio)
        parameters["watermark"] = bool(spec.watermark)
        if spec.seed is not None:
            parameters["seed"] = int(spec.seed)

        body: dict[str, Any] = {
            "model": model_id,
            "input": input_body,
            "parameters": parameters,
        }
        url = f"{self.base_url}{self.CREATE_PATH}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        return "POST", url, headers, body

    async def _build_media(self, spec: VideoGenSpec) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if spec.shape == VideoTaskShape.TEXT2VIDEO:
            return items

        if spec.shape == VideoTaskShape.IMAGE2VIDEO:
            first = next(
                (m for m in spec.images() if m.role == MediaRole.FIRST_FRAME),
                None,
            ) or (spec.images()[0] if spec.images() else None)
            if first is not None:
                url = await self.materialize_media(first.ref)
                if url:
                    items.append({"type": "first_frame", "url": url})
            return items

        if spec.shape == VideoTaskShape.FIRST_LAST_FRAME:
            first = next((m for m in spec.images() if m.role == MediaRole.FIRST_FRAME), None)
            last = next((m for m in spec.images() if m.role == MediaRole.LAST_FRAME), None)
            images = spec.images()
            if first is None and images:
                first = images[0]
            if last is None and len(images) >= 2:
                last = images[1]
            if first is not None:
                url = await self.materialize_media(first.ref)
                if url:
                    items.append({"type": "first_frame", "url": url})
            if last is not None:
                url = await self.materialize_media(last.ref)
                if url:
                    items.append({"type": "last_frame", "url": url})
            return items

        # MULTIMODAL: reference_* / file / link,不写 first_frame/last_frame
        img_urls = await self.materialize_all([m.ref for m in spec.images()[: self.max_images]])
        for url in img_urls:
            if url:
                items.append({"type": "reference_image", "url": url})
        for v in spec.videos()[: self.max_videos]:
            url = await self.materialize_media(v.ref)
            if url:
                items.append({"type": "reference_video", "url": url})
        for a in spec.audios()[: self.max_audios]:
            url = await self.materialize_media(a.ref)
            if url:
                items.append({"type": "reference_audio", "url": url})
        file_url = file_url_of(spec.params)
        link_url = link_url_of(spec.params)
        if file_url:
            items.append({"type": "file", "url": file_url})
        elif link_url:
            items.append({"type": "link", "url": link_url})
        return items


__all__ = [
    "Wan30Provider",
    "Wan30ProviderError",
]
