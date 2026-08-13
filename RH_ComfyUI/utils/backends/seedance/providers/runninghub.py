"""RunningHub Seedance Provider

实现要点(与 ARK / Gateway 差异巨大):
- 多端点: IMAGE2VIDEO/FIRST_LAST_FRAME → /image-to-video;其他 → /multimodal-video
- 扁平 schema: firstFrameUrl / lastFrameUrl 或 imageUrls / videoUrls / audioUrls
- duration 为字符串 ("5")
- 媒体必须为公网 URL —— 需先调用 /openapi/v2/media/upload/binary 上传
- 查询: POST /openapi/v2/query,body {taskId};返回 results[] 中按 outputType 识别视频 URL
- 响应: 裸 JSON(taskId / status / results / usage);无 model 字段
- 复用 rh_app.api.upload_file 拿 fileName,view URL 通过 /view?filename=... 构造
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ..spec import VideoGenSpec, VideoTaskShape
from ..provider import (
    NormalizedTask,
    NormalizedStatus,
    SeedanceProvider,
    normalize_usage,
)
from ....core.types import MediaRef, MediaKind
from ....backends.rh_app.api import rh_app_api

_VIDEO_OUTPUT_TYPES = {"mp4", "mov"}


def _guess_extension(ref: MediaRef, kind: MediaKind) -> str:
    """从文件名/MIME 推断上传时使用的扩展名。"""
    if ref.filename:
        lower = ref.filename.lower()
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".mp3", ".wav"):
            if lower.endswith(ext):
                return ext
    mime = (ref.mime_type or "").lower()
    if "png" in mime:
        return ".png"
    if "jpeg" in mime or "jpg" in mime:
        return ".jpg"
    if "webp" in mime:
        return ".webp"
    if "gif" in mime:
        return ".gif"
    if "mp4" in mime:
        return ".mp4"
    if "quicktime" in mime or "mov" in mime:
        return ".mov"
    if "mpeg" in mime or "mp3" in mime:
        return ".mp3"
    if "wav" in mime:
        return ".wav"
    if kind == MediaKind.IMAGE:
        return ".png"
    if kind == MediaKind.VIDEO:
        return ".mp4"
    return ".bin"


def _view_url(file_name: str) -> str:
    """构造 RunningHub /view?filename=... 直链。"""
    return f"https://www.runninghub.cn/view?filename={file_name}"


# 结构化 [参考图片N] 优先;再匹配裸 图片N / image N
_REF_REWRITE_RE = re.compile(
    r"(\[\s*参考图片\s*(\d+)\s*\])|(\[\s*参考视频\s*(\d+)\s*\])|(\[\s*参考音频\s*(\d+)\s*\])"
    r"|(图片\s*(\d+))|(视频\s*(\d+))|(音频\s*(\d+))"
    r"|(image\s*(\d+))|(video\s*(\d+))|(audio\s*(\d+))",
    re.IGNORECASE,
)


def _rewrite_refs_to_at(prompt: str) -> str:
    """把「[参考图片N]/图片N/…」改写为「@Image N/@Video N/@Audio N」(RunningHub 引用语法)。"""

    def _sub(match: re.Match[str]) -> str:
        # 1-2: [参考图片N]  3-4: [参考视频N]  5-6: [参考音频N]
        # 7-8: 图片N  9-10: 视频N  11-12: 音频N
        # 13-14: image N  15-16: video N  17-18: audio N
        if match.group(1) or match.group(7) or match.group(13):
            n = match.group(2) or match.group(8) or match.group(14)
            return f"@Image {n}"
        if match.group(3) or match.group(9) or match.group(15):
            n = match.group(4) or match.group(10) or match.group(16)
            return f"@Video {n}"
        if match.group(5) or match.group(11) or match.group(17):
            n = match.group(6) or match.group(12) or match.group(18)
            return f"@Audio {n}"
        return match.group(0)

    return _REF_REWRITE_RE.sub(_sub, prompt)


class RunningHubSeedanceProvider(SeedanceProvider):
    """RunningHub Seedance 后端(OpenAPI v2)"""

    name = "runninghub"
    DEFAULT_BASE_URL = "https://www.runninghub.cn"

    EP_I2V = "/openapi/v2/rhart-video/sparkvideo-2.0/image-to-video"
    EP_MM = "/openapi/v2/rhart-video/sparkvideo-2.0/multimodal-video"
    EP_QUERY = "/openapi/v2/query"

    supported_shapes = {
        VideoTaskShape.TEXT2VIDEO,
        VideoTaskShape.IMAGE2VIDEO,
        VideoTaskShape.FIRST_LAST_FRAME,
        VideoTaskShape.MULTIMODAL,
        VideoTaskShape.VIDEO_EDIT,
        VideoTaskShape.VIDEO_EXTEND,
    }
    supported_resolutions = {"480p", "720p", "native1080p", "1080p", "2k", "4k"}
    media_prep_concurrency = 4

    STATUS_MAP: dict[str, NormalizedStatus] = {
        "queued": NormalizedStatus.QUEUED,
        "pending": NormalizedStatus.QUEUED,
        "running": NormalizedStatus.RUNNING,
        "success": NormalizedStatus.SUCCEEDED,
        "succeeded": NormalizedStatus.SUCCEEDED,
        "failed": NormalizedStatus.FAILED,
    }

    def transform_prompt(self, prompt: str) -> str:
        return _rewrite_refs_to_at(prompt)

    async def materialize_media(self, ref: MediaRef) -> Optional[str]:
        """已是公网 http(s) URL 直接透传;否则复用 rh_app 上传,转 view URL。

        参考图先做 Seedance 短边放大,避免透传 <300px 原图。
        """
        if ref.kind == MediaKind.IMAGE:
            from ....image_process import prepare_seedance_image_ref

            ref = await prepare_seedance_image_ref(ref)
        if ref.url and ref.url.lower().startswith(("http://", "https://")):
            return ref.url
        if ref.data is None:
            return ref.url
        ext = _guess_extension(ref, ref.kind)
        file_name = await rh_app_api.upload_file(ref.data, f"input{ext}")
        return _view_url(file_name)

    async def render_create(
        self,
        spec: VideoGenSpec,
        *,
        model: Optional[str],  # noqa: ARG002 — RunningHub 端点即模型,无 model 字段
    ) -> tuple[str, str, dict[str, str], dict[str, Any]]:
        common: dict[str, Any] = {
            "prompt": self.transform_prompt(spec.prompt),
            "resolution": spec.resolution or "720p",
            "duration": str(spec.duration),
            "generateAudio": spec.generate_audio,
            "ratio": spec.ratio or "adaptive",
            "realPersonMode": bool(spec.params.get("real_person_mode", True)),
            "conversionSlots": spec.params.get("conversion_slots", ["all"]),
            "returnLastFrame": spec.return_last_frame,
            "seed": spec.seed if spec.seed is not None else -1,
        }

        if spec.shape in (VideoTaskShape.IMAGE2VIDEO, VideoTaskShape.FIRST_LAST_FRAME):
            ff = spec.first_frame()
            lf = spec.last_frame()

            # 兜底:`ordered_content` 路径下 classify 不会强制回填 role
            # (避免覆盖用户在有序段里显式声明的 role);此处按形态语义
            # 退回到"图 1 = 首帧 / 图 1+2 = 首尾帧"惯例。
            imgs = spec.images()
            if ff is None and imgs:
                ff = imgs[0]
            if lf is None and spec.shape == VideoTaskShape.FIRST_LAST_FRAME and len(imgs) >= 2:
                lf = imgs[1]

            if ff is None:
                from ..provider import SeedanceProviderError

                raise SeedanceProviderError(
                    f"{self.name}: 形态 {spec.shape.value} 缺少首帧图",
                    code="MISSING_FIRST_FRAME",
                    provider=self.name,
                    # 本地校验错误,文案已经用户友好,直接当 user_message 透出。
                    user_message=f"请先提供首帧图片(形态 {spec.shape.value} 需要)。",
                )
            refs = [ff.ref] + ([lf.ref] if lf is not None else [])
            urls = await self.materialize_all(refs)
            body: dict[str, Any] = {**common, "firstFrameUrl": urls[0]}
            if lf is not None:
                body["lastFrameUrl"] = urls[1]
            path = self.EP_I2V
        else:
            imgs = spec.images()
            vids = spec.videos()
            auds = spec.audios()
            all_urls = await self.materialize_all([m.ref for m in imgs + vids + auds])
            ni, nv = len(imgs), len(vids)
            body = {
                **common,
                "imageUrls": all_urls[:ni],
                "videoUrls": all_urls[ni : ni + nv],
                "audioUrls": all_urls[ni + nv :],
            }
            path = self.EP_MM

        return "POST", f"{self.base_url}{path}", self._auth_headers(), body

    def parse_create(self, resp_json: dict[str, Any]) -> str:
        return str(resp_json.get("taskId") or "")

    async def get(self, task_id: str) -> NormalizedTask:
        url = f"{self.base_url}{self.EP_QUERY}"
        j = await self._request("POST", url, headers=self._auth_headers(), json={"taskId": task_id})
        results = j.get("results") or []
        video: Optional[str] = None
        last_frame: Optional[str] = None
        for r in results:
            if not isinstance(r, dict):
                continue
            url_v = r.get("url")
            ot = (r.get("outputType") or "").lower()
            if ot in _VIDEO_OUTPUT_TYPES:
                if video is None:
                    video = url_v
            elif ot in {"png", "jpg", "jpeg", "webp"}:
                if last_frame is None:
                    last_frame = url_v
        if video is None and results:
            first = results[0]
            if isinstance(first, dict):
                video = first.get("url")
        return NormalizedTask(
            id=str(j.get("taskId") or task_id),
            status=self.map_status(str(j.get("status") or "")),
            video_url=video,
            last_frame_url=last_frame,
            usage=normalize_usage("runninghub", j.get("usage") or {}),
            error=j.get("errorMessage"),
            raw=j,
        )


__all__ = ["RunningHubSeedanceProvider"]
