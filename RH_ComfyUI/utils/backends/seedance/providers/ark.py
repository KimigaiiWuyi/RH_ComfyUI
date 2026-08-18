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

from gsuid_core.logger import logger

from ..spec import MediaRole, VideoGenSpec, VideoTaskShape
from ..provider import (
    NormalizedTask,
    DryRunInterrupt,
    NormalizedStatus,
    SeedanceProvider,
    SeedanceProviderError,
    normalize_usage,
)
from ....core.types import MediaKind

# Seedance / 方舟 content[] 协议:每种媒体的 type 键与 payload 键同名。
# 视频必须是 video_url(不是 image_url),音频必须是 audio_url —— 否则上游
# 会按图片校验并返回 "image format is not supported"(VID-4001)。
_TYPE_KEY: dict[MediaKind, str] = {
    MediaKind.IMAGE: "image_url",
    MediaKind.VIDEO: "video_url",
    MediaKind.AUDIO: "audio_url",
}


def _content_type_key(kind: MediaKind) -> str:
    """SpecMedia.kind → content[].type;未知 kind 不静默回落 image。"""
    try:
        return _TYPE_KEY[kind]
    except KeyError as exc:
        raise ValueError(f"Seedance content[] 不支持的媒体 kind: {kind!r}") from exc


class ContentArrayMixin:
    """共享的 content[] 渲染逻辑(ARK 系 content[] 协议的供应商共用)。

    公开导出:外部供应商插件可继承本 mixin 复用有序多模态渲染
    (此前的私有名 ``_ContentArrayMixin`` 保留为别名,勿新增使用)。
    """

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
                # SpecMedia.kind 与 MediaRef.kind 可能因历史路径不一致;
                # 以 ref.kind 为准(构造时已按文件头/mime 纠正),避免视频走 image_url。
                kind = MediaKind(seg.media.ref.kind.value) if seg.media.ref is not None else seg.media.kind
                key = _content_type_key(kind)
                d = {"type": key, key: {"url": url}}
                d["role"] = self._role_str(seg.media.role, kind)

                if kind == MediaKind.IMAGE:
                    img_idx += 1
                    text_parts.append(f"【图片{img_idx}】")
                    images.append(d)
                elif kind == MediaKind.VIDEO:
                    vid_idx += 1
                    text_parts.append(f"【视频{vid_idx}】")
                    videos.append(d)
                elif kind == MediaKind.AUDIO:
                    aud_idx += 1
                    text_parts.append(f"【音频{aud_idx}】")
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
            text = self.transform_prompt(spec.prompt) if isinstance(self, SeedanceProvider) else spec.prompt
            items.append({"type": "text", "text": text})
        for m, url in zip(spec.media, urls):
            if not url:
                continue
            kind = MediaKind(m.ref.kind.value) if m.ref is not None else m.kind
            key = _content_type_key(kind)
            d: dict[str, Any] = {"type": key, key: {"url": url}}
            d["role"] = self._role_str(m.role, kind)
            items.append(d)
        return items


# 向后兼容别名(存量外部代码用私有名导入)
_ContentArrayMixin = ContentArrayMixin


def _is_seedance25_model(model: str | None) -> bool:
    """识别 2.5 vendor model id(ark 日期编码 / 网关点分)。"""
    if not model:
        return False
    m = model.strip().lower()
    return "seedance-2-5" in m or "seedance-2.5" in m or m.endswith("seedance2.5")


# 不把 auto 写入官方 body;auto 视同未指定,走下方回填或省略
_OMNI_REF_TASK_TYPES = frozenset({"reference", "edit", "extend"})

# 官方:仅多模态参考场景合法;文生 / 首帧 / 首尾帧写入会 400 TaskTypeConstraint
_OMNI_REF_SHAPES = frozenset(
    {
        VideoTaskShape.MULTIMODAL,
        VideoTaskShape.VIDEO_EDIT,
        VideoTaskShape.VIDEO_EXTEND,
    }
)


def apply_omni_reference_task_type(
    body: dict[str, Any],
    spec: VideoGenSpec,
    model: Optional[str],
) -> None:
    """2.5 官方字段,与 duration 同级。2.0 不写(上游不认识)。

    仅 MULTIMODAL / VIDEO_EDIT / VIDEO_EXTEND 写入;文生、首帧、首尾帧禁止。
    显式 spec.omni_reference_task_type 优先;否则按 shape 回填 edit/extend/reference。
    """
    if not _is_seedance25_model(model):
        return
    if spec.shape not in _OMNI_REF_SHAPES:
        return
    raw = (spec.omni_reference_task_type or "").strip().lower()
    if raw == "auto":
        raw = ""
    if raw not in _OMNI_REF_TASK_TYPES:
        if spec.shape == VideoTaskShape.VIDEO_EDIT:
            raw = "edit"
        elif spec.shape == VideoTaskShape.VIDEO_EXTEND:
            raw = "extend"
        else:
            # 多参考须由 classify / 调用方显式给 reference;空值不回填 auto
            # (首尾帧+音视频会被判成 MULTIMODAL,回填会把不该出现的 key 写进上游)
            return
    body["omni_reference_task_type"] = raw


class ArkSeedanceProvider(ContentArrayMixin, SeedanceProvider):
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
    # 2.0 支持到 4k;2.5 仅 480p/720p —— 分辨率上限由各模型 schema 约束,这里放宽全集
    supported_resolutions = {"480p", "720p", "1080p", "4k"}
    # Seedance 2.5 放宽到 30s / 图 30 / 视频 10 / 音频 10;2.0 模型侧 max_reference 仍挡 12
    min_duration: int = 4
    max_duration: int = 30
    max_images: int = 30
    max_videos: int = 10
    max_audios: int = 10

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

    def _create_headers(self) -> dict[str, str]:
        """创建请求专用头:网关模式注入 Idempotency-Key,防重复计费。

        只在创建时生成(一次 create 一个 key);查询/删除不需要,ARK 原生
        模式也不需要(端点本身有请求级去重)。
        """
        headers = self._auth_headers()
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
            # 给Web 控制台展示原始 msg,排查信息(code / 信封)仍写在 str(exc)。
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
        # duration=-1 必须原样透传(2.5 自动时长);0/None 不写
        if spec.duration is not None and int(spec.duration) != 0:
            body["duration"] = int(spec.duration)
        if spec.resolution is not None:
            body["resolution"] = spec.resolution
        if spec.seed is not None:
            body["seed"] = spec.seed
        if spec.generate_audio:
            body["generate_audio"] = True
        if not spec.watermark:
            body["watermark"] = False
        # camera_fixed 仅 1.x;2.5(ark/网关 model id)写入会 400
        if spec.camera_fixed and not _is_seedance25_model(model):
            body["camera_fixed"] = True
        if spec.return_last_frame:
            body["return_last_frame"] = True
        if spec.service_tier and spec.service_tier != "default":
            body["service_tier"] = spec.service_tier
        # Seedance 2.5: mp4 / mov;其它系列忽略(上游不认识会报错,仅非空且非默认 mp4 时写)
        out_fmt = (spec.output_format or "").strip().lower()
        if out_fmt and out_fmt != "mp4":
            body["output_format"] = out_fmt
        apply_omni_reference_task_type(body, spec, model)
        draft = spec.params.get("draft")
        if draft is not None:
            body["draft"] = draft
        cb = spec.params.get("callback_url")
        if cb:
            body["callback_url"] = cb
        exec_exp = spec.params.get("execution_expires_after")
        if exec_exp is not None:
            body["execution_expires_after"] = exec_exp
        return "POST", f"{self.base_url}{self._endpoint}", self._create_headers(), body

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

    async def delete(self, task_id: str) -> None:
        """取消排队中任务或删除任务记录(火山方舟 DELETE /contents/generations/tasks/{id})。

        网关模式走同一相对路径 /video/generation/tasks/{id};若网关未实现 DELETE,
        失败由上层记日志后仍取消本地 asyncio 任务。
        """
        if not task_id:
            return
        url = f"{self.base_url}{self._endpoint}/{task_id}"
        logger.info(f"[Seedance:{self.name}] 上游 cancel DELETE task_id={task_id} url={url}")
        # DELETE 无 body;_request 在 dry_run 下会抛 DryRunInterrupt —— 取消路径吞掉
        try:
            await self._request("DELETE", url, headers=self._auth_headers())
            logger.info(f"[Seedance:{self.name}] 上游 cancel 完成 task_id={task_id}")
        except DryRunInterrupt:
            logger.info(f"[Seedance:{self.name}] Dry-Run 跳过 cancel task_id={task_id}")
            return


__all__ = ["ArkSeedanceProvider", "ContentArrayMixin"]
