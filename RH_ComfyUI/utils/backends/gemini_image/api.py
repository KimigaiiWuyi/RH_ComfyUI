"""Gemini 生图客户端 — 走官方 google-genai SDK 的 generate_content

不再手拼 REST/URL:由 SDK 处理端点与鉴权。
- AI Studio(个人版):``Client(api_key=...)``,只需 API Key;
- VertexAI(组织版):``Client(vertexai=True, project=..., location=...)``,鉴权走
  ADC 或服务账号 JSON(SDK 限制:project 与 api_key 互斥,Vertex 不能用 API Key)。

生图必须走 ``models.generate_content``。``interactions.create`` 会把
``gemini-3.1-flash-image-preview`` 改写成 ``…-preview-agent``,该变体
**不接受参考图**(Image input modality is not enabled)。

进程重启后若统计里还留着旧 interaction id,``resume_interaction`` /
``cancel_interaction`` 仍走 Interactions,仅用于历史任务。
"""

from __future__ import annotations

import base64
import asyncio
from typing import Any, Optional

import httpx

from gsuid_core.logger import logger

from ....rh_config.comfyui_config import SERVICE_CONFIG

# background interaction 终态(官方 Interactions API status 枚举)
_TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "cancelled",
        "canceled",
        "incomplete",
    }
)


def _inline_image_part(img: bytes) -> dict[str, Any]:
    """调试/单测用:图片块 mime 必须与字节头一致,不能一律标 png。"""
    from ...image_process import image_mime_from_bytes

    return {
        "type": "image",
        "mime_type": image_mime_from_bytes(img),
        "data": base64.b64encode(img).decode(),
    }


def _canonical_image_model(model: str) -> str:
    """Interactions 会给生图模型加 ``-agent`` 后缀;正式 ID 没有这个尾巴。"""
    name = (model or "").strip()
    if name.endswith("-agent"):
        return name[: -len("-agent")]
    return name


class GeminiImageAPI:
    """Gemini 生图客户端(@property 读配置,改完即时生效)"""

    @property
    def api_key(self) -> str:
        return str(SERVICE_CONFIG.get_config("Gemini_Image_apikey").data or "")

    @property
    def base_url(self) -> str:
        """AI Studio 的中转地址(可选);留空直连官方端点。

        SDK 会在其后拼 ``/v1beta/...``(``api_version`` 不受影响),所以填到中转端
        的路径前缀为止即可,结尾带不带 ``/`` 都行(SDK 自己会规整)。
        """
        return str(SERVICE_CONFIG.get_config("Gemini_Image_BaseURL").data or "").strip()

    @property
    def project_id(self) -> str:
        return str(SERVICE_CONFIG.get_config("Gemini_Image_Project_ID").data or "").strip()

    @property
    def location(self) -> str:
        loc = str(SERVICE_CONFIG.get_config("Gemini_Image_Location").data or "").strip()
        return loc or "global"

    @property
    def sa_file(self) -> str:
        """VertexAI 服务账号 JSON 路径(可选);留空则走 ADC。"""
        return str(SERVICE_CONFIG.get_config("Gemini_Image_SA_File").data or "").strip()

    @property
    def is_vertex(self) -> bool:
        # 显式开关决定模式,不再由 project_id 推断:避免"填了 project 就被迫走
        # Vertex(需 ADC)、api_key 被忽略"的坑。默认关 → 走 AI Studio 用 key。
        return bool(SERVICE_CONFIG.get_config("Gemini_Image_Use_Vertex").data)

    def is_configured(self) -> bool:
        """可用性判定(只读,不发网络):Vertex 看 project,AI Studio 看 key。"""
        return bool(self.project_id) if self.is_vertex else bool(self.api_key)

    def _build_client(self) -> Any:
        from google import genai

        if self.is_vertex:
            credentials = None
            if self.sa_file:
                from google.oauth2 import service_account

                credentials = service_account.Credentials.from_service_account_file(
                    self.sa_file, scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
            return genai.Client(
                vertexai=True,
                project=self.project_id,
                location=self.location,
                credentials=credentials,
            )
        # 中转地址只在 AI Studio 模式生效:Vertex 有自己的端点体系,
        # 把 generativelanguage 的中转前缀套上去只会把它打歪。
        if self.base_url:
            return genai.Client(
                api_key=self.api_key,
                http_options={"base_url": self.base_url},  # type: ignore[arg-type]
            )
        return genai.Client(api_key=self.api_key)

    async def generate(
        self,
        *,
        model: str,
        prompt: str,
        images: Optional[list[bytes]] = None,
        aspect_ratio: str = "1:1",
        image_size: Optional[str] = "2K",
        background: bool = True,
        poll_interval: float = 1.5,
        max_wait: float = 600.0,
    ) -> bytes:
        """生成一张图并返回原始字节;失败抛 RuntimeError(带上游文案)。

        image_size=None 时整个字段不发(一代 gemini-2.5-flash-image 不支持
        image_config.image_size,发了会被上游拒)。

        ``background`` / ``poll_*`` 仅兼容旧调用方,生图已改为单次
        ``generate_content``,不再创建 interaction。
        """
        from google.genai import types

        from ...image_process import image_mime_from_bytes

        _ = (background, poll_interval, max_wait)
        client = self._build_client()
        model = _canonical_image_model(model)

        if images:
            parts: list[Any] = [types.Part.from_text(text=prompt)]
            for img in images:
                parts.append(types.Part.from_bytes(data=img, mime_type=image_mime_from_bytes(img)))
            contents: Any = [types.Content(role="user", parts=parts)]
        else:
            contents = prompt

        img_sizes = ", ".join(f"{len(b)}B" for b in (images or [])) or "无"
        endpoint = self.base_url if (self.base_url and not self.is_vertex) else "官方"
        logger.info(
            f"[Gemini-Image] generate_content model={model} vertex={self.is_vertex} endpoint={endpoint} "
            f"ratio={aspect_ratio} size={image_size or '-'} 参考图={len(images or [])} 张"
        )
        logger.debug(f"[Gemini-Image] 请求 prompt={prompt[:120]!r} 参考图=[{img_sizes}] modalities=['IMAGE']")

        from ...mappers.gemini_image import snap_gemini_aspect_ratio

        aspect_ratio = snap_gemini_aspect_ratio(aspect_ratio)
        image_config_kw: dict[str, Any] = {"aspect_ratio": aspect_ratio}
        if image_size:
            image_config_kw["image_size"] = image_size
        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(**image_config_kw),
        )

        from ....core.telemetry.wire_capture import set_wire_audit

        set_wire_audit(
            prompt=prompt,
            request={
                "model": model,
                "prompt": prompt,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
                "num_images": len(images or []),
                "response_modalities": ["IMAGE"],
                "api": "generate_content",
            },
        )

        response = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )
        logger.info(f"[Gemini-Image] 完成 model={model} {_summarize_generate(response)}")
        logger.debug(f"[Gemini-Image] 原始响应: {_safe_dump(response)}")

        data, uri = _find_generate_image(response)
        if data is not None:
            return data
        if uri:
            logger.info(f"[Gemini-Image] 图片为外链,下载: {uri}")
            return await _download(uri)
        raise RuntimeError(f"Gemini 响应未包含图片: {_summarize_generate(response)}")

    async def cancel_interaction(self, interaction_id: str) -> None:
        """取消 background interaction: ``POST .../interactions/{id}/cancel``。"""
        if not interaction_id:
            return
        client = self._build_client()
        logger.info(
            f"[Gemini-Image] 上游 cancel interactions.cancel id={interaction_id}"
        )
        await client.aio.interactions.cancel(interaction_id)
        logger.info(f"[Gemini-Image] 上游 cancel 完成 interaction_id={interaction_id}")

    async def resume_interaction(self, interaction_id: str) -> bytes:
        """公开:按 interaction id 继续 get+poll 直至出图(resume_poll 入口)。"""
        if not interaction_id:
            raise RuntimeError("缺少 interaction_id")
        client = self._build_client()
        interaction = await client.aio.interactions.get(interaction_id)
        interaction = await self._poll_until_done(
            client, interaction, interval=1.5, max_wait=1800.0
        )
        status = str(getattr(interaction, "status", None) or "").lower()
        if status in {"failed", "cancelled", "canceled"}:
            raise RuntimeError(f"Gemini interaction 失败: status={status}")
        data, uri = _find_image(interaction)
        if data is not None:
            return data
        if uri:
            return await _download(uri)
        raise RuntimeError(f"Gemini 响应未包含图片: status={status}")

    async def _bind_active_cancel(self, client: Any, interaction_id: str) -> None:
        try:
            from ....core.dispatch.active_tasks import get_active_task_registry

            # 失败须向上抛:registry 据此记 cancelled_remote=False,本地仍会 cancel
            async def _cancel_remote() -> None:
                await client.aio.interactions.cancel(interaction_id)
                logger.info(f"[Gemini-Image] 上游已 cancel interaction_id={interaction_id}")

            await get_active_task_registry().bind_vendor_task(
                vendor_task_id=interaction_id,
                cancel_remote=_cancel_remote,
                channel_name="gemini",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"[Gemini-Image] 绑定 vendor 任务失败(忽略): {exc}")

    async def _best_effort_cancel(self, client: Any, interaction_id: str) -> None:
        try:
            from ....core.dispatch.active_tasks import remote_cancel_already_attempted

            if remote_cancel_already_attempted():
                logger.debug(
                    f"[Gemini-Image] 跳过 CancelledError 兜底 cancel"
                    f"(cancel_generation 已尝试上游): {interaction_id}"
                )
                return
        except Exception:  # noqa: BLE001
            pass
        try:
            await client.aio.interactions.cancel(interaction_id)
            logger.info(f"[Gemini-Image] CancelledError 兜底已 cancel {interaction_id}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[Gemini-Image] CancelledError 兜底 cancel 失败: {exc}")

    async def _poll_until_done(
        self,
        client: Any,
        interaction: Any,
        *,
        interval: float,
        max_wait: float,
    ) -> Any:
        """轮询 background interaction 直至终态。

        若 create 已直接返回 completed(少数快速路径),立即返回不再 poll。
        """
        loop = asyncio.get_event_loop()
        start = loop.time()
        iid = str(getattr(interaction, "id", None) or "")
        status = str(getattr(interaction, "status", None) or "").lower()
        if status in _TERMINAL_STATUSES or (not status and _find_image(interaction)[0] is not None):
            # 已有终态或同步带回图片
            if status not in _TERMINAL_STATUSES and _find_image(interaction)[0] is not None:
                return interaction
            if status in _TERMINAL_STATUSES:
                return interaction

        poll_n = 0
        while True:
            status = str(getattr(interaction, "status", None) or "").lower()
            if status in _TERMINAL_STATUSES:
                return interaction
            # 无 status 但已有图(兼容)
            if _find_image(interaction)[0] is not None or _find_image(interaction)[1]:
                return interaction

            if loop.time() - start > max_wait:
                raise RuntimeError(f"Gemini background interaction 超时({max_wait:.0f}s): id={iid} status={status}")

            await asyncio.sleep(interval)
            poll_n += 1
            if not iid:
                raise RuntimeError("Gemini background interaction 缺少 id,无法轮询")
            interaction = await client.aio.interactions.get(iid)
            if poll_n % 10 == 0:
                logger.info(
                    f"[Gemini-Image] 轮询 interaction_id={iid} status={getattr(interaction, 'status', None)} n={poll_n}"
                )


def _get(obj: Any, key: str) -> Any:
    """兼容取值:steps 是未声明的 extra 字段(原始 dict),outputs 是 pydantic 对象。"""
    return obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)


def _block_image(block: Any) -> tuple[Optional[bytes], Optional[str]]:
    if _get(block, "type") != "image":
        return None, None
    raw = _get(block, "data")
    if raw:
        return (raw if isinstance(raw, bytes) else base64.b64decode(raw)), None
    uri = _get(block, "uri")
    return (None, str(uri)) if uri else (None, None)


def _iter_generate_parts(response: Any) -> list[Any]:
    parts = getattr(response, "parts", None)
    if parts:
        return list(parts)
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    content = getattr(candidates[0], "content", None)
    return list(getattr(content, "parts", None) or []) if content is not None else []


def _find_generate_image(response: Any) -> tuple[Optional[bytes], Optional[str]]:
    """从 generate_content 响应里取第一张图(inline_data 或 file_data.uri)。"""
    for part in _iter_generate_parts(response):
        inline = getattr(part, "inline_data", None)
        if inline is not None:
            raw = getattr(inline, "data", None)
            if raw:
                return (raw if isinstance(raw, bytes) else base64.b64decode(raw)), None
            uri = getattr(inline, "uri", None)
            if uri:
                return None, str(uri)
        file_data = getattr(part, "file_data", None)
        if file_data is not None:
            uri = getattr(file_data, "file_uri", None) or getattr(file_data, "uri", None)
            if uri:
                return None, str(uri)
    return None, None


def _summarize_generate(response: Any) -> str:
    kinds: list[str] = []
    for part in _iter_generate_parts(response):
        if getattr(part, "inline_data", None) is not None:
            kinds.append("inline_image")
        elif getattr(part, "file_data", None) is not None:
            kinds.append("file_image")
        elif getattr(part, "text", None):
            kinds.append("text")
        else:
            kinds.append("?")
    return "parts=[" + ", ".join(kinds) + "]" if kinds else "parts=(空)"


def _find_image(interaction: Any) -> tuple[Optional[bytes], Optional[str]]:
    """找第一张图:先 outputs(顶层 Content),再 steps[*].content[*](含 model_output 步)。"""
    for block in _get(interaction, "outputs") or []:
        d, u = _block_image(block)
        if d or u:
            return d, u
    for step in _steps(interaction):
        for block in _get(step, "content") or []:
            d, u = _block_image(block)
            if d or u:
                return d, u
    return None, None


def _steps(interaction: Any) -> list:
    """steps 是 SDK 未声明的 extra 字段;getattr 拿不到时回落 model_extra。"""
    steps = getattr(interaction, "steps", None)
    if steps is None:
        extra = getattr(interaction, "model_extra", None) or {}
        steps = extra.get("steps")
    return steps or []


def _summarize(interaction: Any) -> str:
    """概括 outputs 与 steps 的结构便于排查(不打印大 base64)。"""

    def _blocks(items: Any) -> str:
        out: list[str] = []
        for it in items or []:
            t = _get(it, "type") or "?"
            fields = [f for f in ("data", "uri", "text", "content") if _get(it, f)]
            out.append(f"{t}({','.join(fields) or 'empty'})")
        return "[" + ", ".join(out) + "]" if out else "(空)"

    return f"outputs={_blocks(_get(interaction, 'outputs'))} steps={_blocks(_steps(interaction))}"


def _safe_dump(interaction: Any) -> str:
    """脱敏概览:优先 pydantic model_dump,截断避免刷屏(base64 会很长)。"""
    dump = getattr(interaction, "model_dump", None)
    try:
        text = str(dump()) if callable(dump) else repr(interaction)
    except Exception:  # noqa: BLE001
        text = repr(interaction)
    return text[:1500]


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


gemini_image_api = GeminiImageAPI()

__all__ = ["GeminiImageAPI", "gemini_image_api"]
