"""火山方舟 Seedance Provider

实现要点:
- 端点: POST /contents/generations/tasks(ARK 原生) 或 /video/generation/tasks(网关模式)
- 请求体: content[] 有序多模态数组 + 通用参数
- duration 为 int
- GET /contents/generations/tasks/{id} 查询(ARK) 或 /video/generation/tasks/{id}(网关)
- 响应: ARK 裸 JSON,字段 `id` / `content.video_url` / `content.last_frame_url`; 网关 {code,msg,data} 信封
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from ..spec import MediaRole, VideoGenSpec, VideoTaskShape
from ..provider import (
    NormalizedTask,
    NormalizedStatus,
    SeedanceProvider,
    SeedanceProviderError,
    normalize_usage,
)
from ....core.types import MediaKind

_TYPE_KEY: dict[MediaKind, str] = {
    MediaKind.IMAGE: "image_url",
    MediaKind.VIDEO: "video_url",
    MediaKind.AUDIO: "audio_url",
}


class _ContentArrayMixin:
    """共享的 content[] 渲染逻辑(ARK / Gateway 共用)。"""

    @staticmethod
    def _role_str(role: MediaRole, kind: MediaKind) -> str:
        if role == MediaRole.FIRST_FRAME:
            return "first_frame"
        if role == MediaRole.LAST_FRAME:
            return "last_frame"
        return f"reference_{kind.value}"

    async def _build_content(self, spec: VideoGenSpec) -> list[dict[str, Any]]:
        """按 `ordered_segments` 渲染 content[];无序段时回退到 prompt+media。

        Seedance API 限制: content[] 中最多只能有一个 text 项。
        因此将所有 text 段合并为一个,媒体引用以 "图片1"/"视频1"/"音频1" 形式嵌入文本,
        媒体项按图片→视频→音频顺序附在文字之后。
        """
        if spec.ordered_segments:
            # 有序路径:并行 materialize 所有媒体
            media_refs = [s.media.ref for s in spec.ordered_segments if s.kind == "media" and s.media]
            if isinstance(self, SeedanceProvider):
                urls = await self.materialize_all(media_refs)
            else:
                urls = [None] * len(media_refs)

            url_iter = iter(urls)
            text_parts: list[str] = []
            images: list[dict[str, Any]] = []
            videos: list[dict[str, Any]] = []
            audios: list[dict[str, Any]] = []
            img_idx = 0
            vid_idx = 0
            aud_idx = 0

            for seg in spec.ordered_segments:
                if seg.kind == "text":
                    if seg.text:
                        txt = self.transform_prompt(seg.text) if isinstance(self, SeedanceProvider) else seg.text
                        text_parts.append(txt)
                    continue
                if seg.media is None:
                    continue
                url = next(url_iter, None)
                if not url:
                    continue
                key = _TYPE_KEY[seg.media.kind]
                d: dict[str, Any] = {"type": key, key: {"url": url}}
                d["role"] = self._role_str(seg.media.role, seg.media.kind)

                if seg.media.kind == MediaKind.IMAGE:
                    img_idx += 1
                    text_parts.append(f"图片{img_idx}")
                    images.append(d)
                elif seg.media.kind == MediaKind.VIDEO:
                    vid_idx += 1
                    text_parts.append(f"视频{vid_idx}")
                    videos.append(d)
                elif seg.media.kind == MediaKind.AUDIO:
                    aud_idx += 1
                    text_parts.append(f"音频{aud_idx}")
                    audios.append(d)

            items: list[dict[str, Any]] = []
            if text_parts:
                items.append({"type": "text", "text": "".join(text_parts)})
            items.extend(images)
            items.extend(videos)
            items.extend(audios)
            return items

        # 兜底路径:无 ordered_segments 时,prompt + 扁平 media(老 provider 行为)
        if isinstance(self, SeedanceProvider):
            urls = await self.materialize_all([m.ref for m in spec.media])
        else:  # 兜底:逐项同步准备(不应触发,仅类型系统防御)
            urls = [None] * len(spec.media)

        items = []
        if spec.prompt:
            text = (
                self.transform_prompt(spec.prompt)
                if isinstance(self, SeedanceProvider)
                else spec.prompt
            )
            items.append({"type": "text", "text": text})
        for m, url in zip(spec.media, urls):
            if not url:
                continue
            key = _TYPE_KEY[m.kind]
            d: dict[str, Any] = {"type": key, key: {"url": url}}
            d["role"] = self._role_str(m.role, m.kind)
            items.append(d)
        return items


class ArkSeedanceProvider(_ContentArrayMixin, SeedanceProvider):
    """火山方舟 Seedance 官方后端

    当 base_url 为非 ARK 默认值(通常为聚合网关)时,
    自动切换为网关端点 /video/generation/tasks 并解网关 {code,msg,data} 信封。
    """

    name = "ark"
    DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

    # 网关模式端点(聚合网关 base_url 时使用)
    _GATEWAY_ENDPOINT = "/video/generation/tasks"
    # ARK 原生端点
    _ARK_ENDPOINT = "/contents/generations/tasks"

    supported_shapes = {
        VideoTaskShape.TEXT2VIDEO,
        VideoTaskShape.IMAGE2VIDEO,
        VideoTaskShape.FIRST_LAST_FRAME,
        VideoTaskShape.MULTIMODAL,
        VideoTaskShape.VIDEO_EDIT,
        VideoTaskShape.VIDEO_EXTEND,
    }
    supported_resolutions = {"480p", "720p", "1080p", "4k"}

    STATUS_MAP: dict[str, NormalizedStatus] = {
        "queued": NormalizedStatus.QUEUED,
        "running": NormalizedStatus.RUNNING,
        "succeeded": NormalizedStatus.SUCCEEDED,
        "failed": NormalizedStatus.FAILED,
        "expired": NormalizedStatus.EXPIRED,
        "cancelled": NormalizedStatus.CANCELLED,
    }

    @property
    def _is_gateway_mode(self) -> bool:
        """检测当前是否运行在网关模式(非 ARK 原生 base_url)。"""
        return self.base_url != self.DEFAULT_BASE_URL

    @property
    def _endpoint(self) -> str:
        """根据运行模式返回正确的端点路径。"""
        return self._GATEWAY_ENDPOINT if self._is_gateway_mode else self._ARK_ENDPOINT

    def _auth_headers(self) -> dict[str, str]:
        """网关模式下注入 Idempotency-Key,防重试重复计费。

        ARK 原生模式不需要 —— ARK 端点本身有请求级去重。
        网关模式与 GatewaySeedanceProvider 行为一致。
        """
        headers = super()._auth_headers()
        if self._is_gateway_mode:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        return headers

    def _unwrap(self, resp_json: dict[str, Any]) -> dict[str, Any]:
        """网关模式: 解 {code,msg,data} 信封; ARK 模式: 原样返回。"""
        if not self._is_gateway_mode:
            return resp_json
        code = resp_json.get("code")
        # 容错:网关 code 偶尔返回字符串/非数值,不要让 int() 抛 ValueError
        try:
            code_int = int(code) if code is not None else None
        except (TypeError, ValueError):
            code_int = None
        if code_int is not None and 200 <= code_int < 300:
            data = resp_json.get("data")
            return data if isinstance(data, dict) else {}
        data = resp_json.get("data") or {}
        msg = data.get("message") or resp_json.get("msg") or "网关错误"
        raise SeedanceProviderError(
            f"网关错误 {code}: {msg}",
            code=str(data.get("code") or "GATEWAY_ERROR"),
            retryable=bool(data.get("retryable", False)),
            http_status=code_int if code_int is not None else 500,
            provider=self.name,
            # 给前端展示原始 msg,排查信息(code / 信封)仍写在 str(exc)。
            user_message=str(msg),
        )

    async def render_create(
        self,
        spec: VideoGenSpec,
        *,
        model: Optional[str],
    ) -> tuple[str, str, dict[str, str], dict[str, Any]]:
        content = await self._build_content(spec)
        body: dict[str, Any] = {"content": content}
        if model:
            body["model"] = model
        if spec.ratio is not None:
            body["ratio"] = spec.ratio
        if spec.duration:
            body["duration"] = int(spec.duration)
        if spec.resolution is not None:
            body["resolution"] = spec.resolution
        if spec.seed is not None:
            body["seed"] = spec.seed
        if spec.generate_audio:
            body["generate_audio"] = True
        if not spec.watermark:
            body["watermark"] = False
        if spec.camera_fixed:
            body["camera_fixed"] = True
        if spec.return_last_frame:
            body["return_last_frame"] = True
        if spec.service_tier and spec.service_tier != "default":
            body["service_tier"] = spec.service_tier
        draft = spec.params.get("draft")
        if draft is not None:
            body["draft"] = draft
        cb = spec.params.get("callback_url")
        if cb:
            body["callback_url"] = cb
        exec_exp = spec.params.get("execution_expires_after")
        if exec_exp is not None:
            body["execution_expires_after"] = exec_exp
        return "POST", f"{self.base_url}{self._endpoint}", self._auth_headers(), body

    def parse_create(self, resp_json: dict[str, Any]) -> str:
        d = self._unwrap(resp_json)
        if self._is_gateway_mode:
            return str(d.get("taskId") or "")
        return str(d.get("id") or "")

    async def get(self, task_id: str) -> NormalizedTask:
        url = f"{self.base_url}{self._endpoint}/{task_id}"
        j = await self._request("GET", url, headers=self._auth_headers())
        if self._is_gateway_mode:
            d = self._unwrap(j)
            return NormalizedTask(
                id=str(d.get("taskId") or task_id),
                status=self.map_status(str(d.get("status") or "")),
                video_url=d.get("resultUrl"),
                last_frame_url=d.get("thumbnailUrl"),
                progress=d.get("progress"),
                usage=normalize_usage("gateway", d.get("tokenUsage") or {}),
                error=d.get("failReason"),
                raw=j,
            )
        content_obj = j.get("content") or {}
        error_obj = j.get("error") or {}
        return NormalizedTask(
            id=str(j.get("id") or task_id),
            status=self.map_status(str(j.get("status") or "")),
            video_url=content_obj.get("video_url"),
            last_frame_url=content_obj.get("last_frame_url"),
            usage=normalize_usage("ark", j.get("usage") or {}),
            error=error_obj.get("message"),
            raw=j,
        )


__all__ = ["ArkSeedanceProvider"]
