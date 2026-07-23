"""Seedream Adapter — 火山方舟 Seedream 5.0 图片生成后端执行器

设计要点(与 gpt_image2 / minimax 对齐):
1. 走同步 POST,无轮询:capabilities.mode = "sync"
2. 凭证热更新:executor.execute() 入口先 refresh_config(),
   避免 Process 内 key 缓存到启动时刻那一帧(§11.2.A)
3. 空 key 直接 raise,不在 _headers() 拼 "Bearer "(§11.3 红线 1)
4. AdapterChannel.invoke(models/bridge.py:53) 捕获 RuntimeError 后
   包成 ChannelError(retryable=True),实现供应商间自动 failover
5. 参考图走 R2 外链(ARK 对 base64 body 限 ~2.4MB,超则 413):
   executor 在调 mapper 前把 bytes 上传 R2,注入 request.params["_image_urls"]
"""

from __future__ import annotations

import io
import asyncio

from PIL import Image

from .api import seedream_api
from ..base import Adapter
from ...core.types import NodeOutput, ProgressEvent, CapabilityManifest
from ...core.request import GenerationRequest
from ...core.pipeline import NodeDef


class SeedreamAdapter(Adapter):
    """Seedream 5.0 (Lite / Pro) 后端适配器"""

    name = "seedream"

    def __init__(self) -> None:
        self.api = seedream_api

    async def check_available(self) -> bool:
        self.api.refresh_config()
        return bool(self.api.enabled and self.api.api_key and self.api.base_url)

    async def get_unavailable_reason(self) -> str:
        self.api.refresh_config()
        if not self.api.enabled:
            return "未启用火山 ARK 供应商,请在 Web 控制台启用 Seedance_Enable_ark"
        if not self.api.api_key:
            return "未配置火山 ARK API Key,请在 Web 控制台配置 Seedance_apikey_ark"
        if not self.api.base_url:
            return "未配置火山 ARK Base URL,请在 Web 控制台配置 Seedance_BaseURL_ark"
        return "Seedream 供应商不可用"

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            supported_tasks=["image"],
            supported_params=["prompt", "images", "ratio", "size_mode", "output_format"],
            output_mime=["image/png", "image/jpeg"],
            mode="sync",
            priority=65,
        )

    async def execute(
        self,
        request: GenerationRequest,
        node: NodeDef,
        *,
        on_progress=None,
    ) -> NodeOutput:
        # ★ 每次请求入口刷一下凭证(§11.2.A 范式:executor 负责兜底)
        self.api.refresh_config()
        # 前置守卫:key 缺失时抛中文 RuntimeError,而不是带 "Bearer " 飞出去
        self.api._require_api_key()

        if node.mapper_func is None:
            raise RuntimeError(f"Seedream 节点 {node.name} 缺少 mapper_func")

        # 注入 vendor model id(Lite / Pro 各取 node.backend_model)
        if node.backend_model:
            request.params["model"] = node.backend_model

        # 参考图走 R2 外链(避免 base64 body 超限 413)
        if request.images:
            await self._materialize_images(request.images, request.params)

        await _emit(on_progress, ProgressEvent(stage="running", percent=15, message="Seedream 生成中"))
        result = await node.mapper_func(request, self.api)
        await _emit(on_progress, ProgressEvent(stage="done", percent=100, message="完成"))

        # mapper 约定返回 NodeOutput;兼容 PIL.Image / bytes 三种形态
        if isinstance(result, NodeOutput):
            return result
        if isinstance(result, Image.Image):
            def _to_png(img: Image.Image) -> bytes:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()

            return NodeOutput(
                status="ok",
                output_type="image",
                data=await asyncio.to_thread(_to_png, result),
                mime_type="image/png",
            )
        if isinstance(result, bytes):
            return NodeOutput(
                status="ok",
                output_type="image",
                data=result,
                mime_type="image/png",
            )
        raise RuntimeError(
            f"Seedream 节点 {node.name} 返回了无法处理的类型: {type(result)}"
        )

    async def _materialize_images(
        self, images: list[bytes], params: dict
    ) -> None:
        """bytes 列表 → R2 外链,注入 params["_image_urls"]

        R2 失败抛 RuntimeError,让上层 ChannelError(retryable=True) 触发 failover。
        """
        try:
            from aigc_system.aifoundation.media_host import materialize
            from aigc_system.aifoundation.media_host import MediaPublishError
        except ImportError:
            # canvas_backend 未装 — 回落 data URL,由 mapper 自己处理
            return

        try:
            urls = [
                u
                for u in await asyncio.gather(
                    *(materialize(img, "image/png") for img in images)
                )
                if u
            ]
            if urls:
                params["_image_urls"] = urls
        except MediaPublishError as exc:
            raise RuntimeError(f"Seedream 输入图上传 R2 失败: {exc}") from exc


# 向后兼容
SeedreamBackend = SeedreamAdapter


async def _emit(cb, event: ProgressEvent) -> None:
    if cb is None:
        return
    try:
        await cb(event)
    except Exception:  # noqa: BLE001
        pass


# asyncio 引用(给 _materialize_images 用)
import asyncio  # noqa: E402, F401
