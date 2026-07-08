"""Gemini 生图客户端 — 走官方 google-genai SDK 的 Interactions API

不再手拼 REST/URL:由 SDK 处理端点与鉴权。
- AI Studio(个人版):``Client(api_key=...)``,只需 API Key;
- VertexAI(组织版):``Client(vertexai=True, project=..., location=...)``,鉴权走
  ADC 或服务账号 JSON(SDK 限制:project 与 api_key 互斥,Vertex 不能用 API Key)。

图像输出:interactions.create(response_modalities=["image"],
generation_config={"image_config": {...}}),结果在 interaction.outputs 里取
type=="image" 的块(data 为 base64,或 uri 为外链)。
"""

from __future__ import annotations

import base64
from typing import Any, Optional

import httpx

from gsuid_core.logger import logger

from ....rh_config.comfyui_config import SERVICE_CONFIG


class GeminiImageAPI:
    """Gemini 生图客户端(@property 读配置,改完即时生效)"""

    @property
    def api_key(self) -> str:
        return str(SERVICE_CONFIG.get_config("Gemini_Image_apikey").data or "")

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
        return genai.Client(api_key=self.api_key)

    async def generate(
        self,
        *,
        model: str,
        prompt: str,
        images: Optional[list[bytes]] = None,
        aspect_ratio: str = "1:1",
        image_size: str = "2K",
    ) -> bytes:
        """生成一张图并返回原始字节;失败抛 RuntimeError(带上游文案)。"""
        client = self._build_client()

        if images:
            parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            for img in images:
                parts.append({"type": "image", "mime_type": "image/png", "data": base64.b64encode(img).decode()})
            model_input: Any = parts
        else:
            model_input = prompt

        img_sizes = ", ".join(f"{len(b)}B" for b in (images or [])) or "无"
        logger.info(
            f"[Gemini-Image] interactions.create model={model} vertex={self.is_vertex} "
            f"ratio={aspect_ratio} size={image_size} 参考图={len(images or [])} 张"
        )
        logger.debug(f"[Gemini-Image] 请求 prompt={prompt[:120]!r} 参考图=[{img_sizes}] modalities=['image']")
        interaction = await client.aio.interactions.create(
            model=model,
            input=model_input,
            response_modalities=["image"],
            generation_config={"image_config": {"aspect_ratio": aspect_ratio, "image_size": image_size}},
        )

        status = getattr(interaction, "status", None)
        logger.info(f"[Gemini-Image] 完成 status={status} {_summarize(interaction)}")
        logger.debug(f"[Gemini-Image] 原始响应: {_safe_dump(interaction)}")

        if status == "failed":
            raise RuntimeError(f"Gemini interaction 失败: {_safe_dump(interaction)}")

        data, uri = _find_image(interaction)
        if data is not None:
            return data
        if uri:
            logger.info(f"[Gemini-Image] 图片为外链,下载: {uri}")
            return await _download(uri)
        raise RuntimeError(f"Gemini 响应未包含图片: status={status}, {_summarize(interaction)}")


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
