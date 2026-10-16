"""Azure OpenAI images 客户端。

与 OpenAI 兼容生图的差别:
- 鉴权头 ``api-key``(空凭证不写这个头);
- URL 走 Foundry v1:``/openai/v1/images/generations|edits?api-version=preview``;
- body/form 的 ``model`` 等于部署名,默认就是模型 id;
- 文生 JSON 不带 ``response_format``;
- 编辑走 multipart:单图 ``image``、多图 ``image[]``;
- 2.5 的 ``xhigh`` / ``max`` 原样透传。
HTTP 失败保留 ``error.code``。404 不在本客户端里重试成 500。日志前缀 ``[AzureImage]``。
"""

from __future__ import annotations

import json
import asyncio
from typing import TypedDict

import aiohttp
from PIL import Image

from gsuid_core.logger import logger

from .catalog import AZURE_IMAGE_MODEL_NAMES
from ...audit_key import mask_api_key
from ..http_retry import is_network_error, call_with_network_retry
from ..gpt_image2.api import GPTImage2API
from ....core.base.errors import ChannelError
from ...mappers.gpt_image2_params import DEFAULT_GPT_IMAGE2_OUTPUT_FORMAT

# 日期版 api-version 属于旧 deployments 路径;v1 的 query 是 preview。
_LEGACY_API_VERSIONS: frozenset[str] = frozenset({"2025-04-01-preview"})
_TRANSIENT_HTTP: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})
_CHANNEL = "azure"


class _AzureImageBody(TypedDict):
    model: str
    prompt: str
    n: int
    size: str
    quality: str
    background: str
    output_format: str


def _cfg(key: str) -> object:
    from ....rh_config.comfyui_config import SERVICE_CONFIG

    return SERVICE_CONFIG.get_config(key).data


def credentials_ready() -> bool:
    """启用 + 凭证 + 终结点。部署勾选由 ``is_deployment_enabled`` 另判。"""
    return bool(_cfg("Azure_Image_Enable") and _cfg("Azure_Image_apikey") and _cfg("Azure_Image_Endpoint"))


def is_deployment_enabled(model_name: str) -> bool:
    """该模型是否在 Azure_Image_Deployment 勾选里。清单外的名字一律 False。"""
    if model_name not in AZURE_IMAGE_MODEL_NAMES:
        return False
    raw = _cfg("Azure_Image_Deployment")
    if not isinstance(raw, list):
        return False
    return model_name in {item for item in raw if isinstance(item, str)}


def max_concurrency() -> int:
    raw = _cfg("Azure_Image_Max_Concurrency") or "2"
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 2


def audit_key_prefix() -> str:
    raw = _cfg("Azure_Image_apikey")
    return mask_api_key(raw if isinstance(raw, str) else "")


def wire_api_version(raw: str) -> str:
    """v1 路径用 preview。旧日期版配置自动改写,避免 query 与路径不匹配。"""
    value = (raw or "").strip()
    if not value or value in _LEGACY_API_VERSIONS:
        return "preview"
    return value


def azure_http_error(status: int, body: str) -> ChannelError:
    """把错误信封翻成 ChannelError,保留 error.code(例如 DeploymentNotFound)。"""
    code = f"HTTP_{status}"
    message = body.strip()[:300] if body.strip() else f"HTTP {status}"
    parsed: object
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict) and "error" in parsed:
        err = parsed["error"]
        if isinstance(err, dict):
            raw_code = err["code"] if "code" in err else None
            if isinstance(raw_code, str) and raw_code:
                code = raw_code
            raw_message = err["message"] if "message" in err else None
            if isinstance(raw_message, str) and raw_message:
                message = raw_message
    transient = status in _TRANSIENT_HTTP
    retryable = transient or status in {404, 408, 429, 500, 502, 503, 504}
    user_message = message
    if code == "DeploymentNotFound":
        user_message = (
            "Azure 找不到该部署。Foundry 里的部署名必须与模型 id 完全一致"
            "(例如 gpt-image-2.5-flare / gpt-image-2.5-sunburst)。"
        )
    return ChannelError(
        f"Azure {code}: {message}",
        retryable=retryable,
        transient=transient,
        channel=_CHANNEL,
        code=code,
        user_message=user_message,
    )


class AzureGPTImage2API(GPTImage2API):
    """Azure images 客户端(v1 路径,model=Foundry 部署名)。"""

    edits_url: str = ""

    @property
    def api_key(self) -> str:
        raw = _cfg("Azure_Image_apikey")
        return raw if isinstance(raw, str) else ""

    @property
    def base_url(self) -> str:
        raw = _cfg("Azure_Image_Endpoint")
        text = raw if isinstance(raw, str) else ""
        return text.rstrip("/")

    @property
    def api_version(self) -> str:
        raw = _cfg("Azure_Image_ApiVersion")
        if isinstance(raw, str) and raw.strip():
            return raw
        return "preview"

    def urls_for(self, _deployment: str) -> tuple[str, str]:
        """Foundry v1 生图/编辑 URL。部署名走 body.model,不进 path。"""
        root = f"{self.base_url}/openai/v1/images"
        suffix = f"?api-version={wire_api_version(self.api_version)}"
        return f"{root}/generations{suffix}", f"{root}/edits{suffix}"

    def update_urls(self) -> None:
        # 真实 URL 在 draw_image 里拼;这里只占位,避免沿用父类的 /v1/images。
        self.images_url = ""
        self.edits_url = ""
        self.chat_url = ""

    def refresh_config(self) -> None:
        self.update_urls()

    async def _base_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: _AzureImageBody | None = None,
        data: aiohttp.FormData | None = None,
    ) -> dict[str, object]:
        logger.info(f"[AzureImage] 请求: {method} {url}")

        async def _once() -> dict[str, object]:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, headers=headers, json=json_body, data=data) as resp:
                    logger.info(f"[AzureImage] 响应状态: {resp.status}")
                    if resp.status != 200:
                        err_body = await resp.text()
                        logger.warning(f"[AzureImage] 错误响应体: {err_body[:500]}")
                        raise azure_http_error(resp.status, err_body)
                    resp_data: object = await resp.json()
                    logger.debug(f"[AzureImage] 响应数据: {resp_data}")
                    if not isinstance(resp_data, dict):
                        raise ChannelError(
                            f"Azure 响应不是 JSON 对象: {type(resp_data)}",
                            retryable=True,
                            channel=_CHANNEL,
                            user_message="Azure 生图失败,请稍后重试。",
                        )
                    out: dict[str, object] = {}
                    for key, value in resp_data.items():
                        if isinstance(key, str):
                            out[key] = value
                    return out

        try:
            return await call_with_network_retry(_once, label=f"{method} {url}")
        except ChannelError:
            raise
        except Exception as exc:
            if is_network_error(exc):
                logger.warning(f"[AzureImage] 请求失败(网络重试已耗尽): {exc}")
                raise ChannelError(
                    f"Azure 网络错误: {exc}",
                    retryable=True,
                    channel=_CHANNEL,
                    code="PROVIDER_NETWORK_ERROR",
                    user_message="Azure 生图网络异常,请稍后重试。",
                ) from exc
            logger.warning(f"[AzureImage] 请求失败: {exc}")
            raise ChannelError(
                f"Azure 请求失败: {exc}",
                retryable=True,
                channel=_CHANNEL,
                user_message="Azure 生图失败,请稍后重试。",
            ) from exc

    async def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json: _AzureImageBody | None = None,
        data: aiohttp.FormData | None = None,
        max_retries: int = 3,
    ) -> dict[str, object] | int:
        merged: dict[str, str] = dict(headers or {})
        if self.api_key:
            merged["api-key"] = self.api_key
        last_error: ChannelError | None = None
        for attempt in range(max_retries):
            try:
                return await self._base_request(method, url, merged, json, data)
            except ChannelError as exc:
                last_error = exc
                if not exc.transient or attempt + 1 >= max_retries:
                    raise
                logger.warning(f"[AzureImage] 瞬时失败 {exc.code},重试 ({attempt + 1}/{max_retries})")
                await asyncio.sleep(1)
        if last_error is not None:
            raise last_error
        raise ChannelError(
            "Azure 请求失败",
            retryable=True,
            channel=_CHANNEL,
            user_message="Azure 生图失败,请稍后重试。",
        )

    async def draw_image(
        self,
        model: str,
        prompt: str,
        aspect_ratio: str | None = "1:1",
        image_size: str | None = "2K",
        quality: str | None = "medium",
        background: str | None = "auto",
        output_format: str | None = DEFAULT_GPT_IMAGE2_OUTPUT_FORMAT,
        image_list: list[bytes] | None = None,
    ) -> Image.Image | int:
        """按有没有参考图分流:generations(JSON) / edits(multipart)。"""
        deployment = (model or "").strip()
        if not deployment:
            raise ChannelError(
                "Azure 缺少部署名(model)",
                retryable=False,
                channel=_CHANNEL,
                user_message="Azure 生图缺少模型名。",
            )
        images_url, edits_url = self.urls_for(deployment)
        size = self.resolve_size(aspect_ratio or "1:1", image_size or "2K")
        quality_s = quality or "medium"
        background_s = background or "auto"
        format_s = output_format or DEFAULT_GPT_IMAGE2_OUTPUT_FORMAT

        if image_list:
            logger.info(f"[AzureImage] 编辑请求: model={deployment}, 参考图={len(image_list)}")
            form = aiohttp.FormData()
            form.add_field("model", deployment)
            form.add_field("prompt", prompt)
            form.add_field("n", "1")
            form.add_field("size", size)
            form.add_field("quality", quality_s)
            form.add_field("background", background_s)
            form.add_field("output_format", format_s)
            image_field = "image" if len(image_list) == 1 else "image[]"
            for index, raw in enumerate(image_list):
                form.add_field(
                    image_field,
                    raw,
                    filename=f"image_{index}.png",
                    content_type="image/png",
                )
            self._remember_wire(
                prompt,
                {
                    "url": edits_url,
                    "model": deployment,
                    "prompt": prompt,
                    "n": 1,
                    "size": size,
                    "quality": quality_s,
                    "background": background_s,
                    "output_format": format_s,
                    "images": list(image_list),
                },
            )
            resp = await self._request(
                "POST",
                edits_url,
                headers={"Accept": "application/json"},
                data=form,
            )
        else:
            logger.info(f"[AzureImage] 生图请求: model={deployment}, size={size}")
            body: _AzureImageBody = {
                "model": deployment,
                "prompt": prompt,
                "n": 1,
                "size": size,
                "quality": quality_s,
                "background": background_s,
                "output_format": format_s,
            }
            self._remember_wire(prompt, body)
            resp = await self._request(
                "POST",
                images_url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                json=body,
            )
        if isinstance(resp, int):
            raise ChannelError(
                f"Azure {deployment} 生成失败，错误码: {resp}",
                retryable=True,
                channel=_CHANNEL,
                user_message=f"Azure {deployment} 生图失败,请稍后重试。",
            )
        return await self._parse_images_response(resp)

    @staticmethod
    def _remember_wire(prompt: str, body: object) -> None:
        # 统计 prompt / request_body 以这次实际上游载荷为准。
        from ....core.telemetry.wire_capture import set_wire_from_http_body

        set_wire_from_http_body(body, prompt=prompt)

    async def _parse_images_response(self, resp: dict[str, object]) -> Image.Image | int:
        """解析 data[0].url 或 b64_json。"""
        try:
            items = resp["data"] if "data" in resp else None
            if not isinstance(items, list) or not items:
                raise ChannelError(
                    "Azure 响应没有图片 data",
                    retryable=True,
                    channel=_CHANNEL,
                    user_message="Azure 未返回图片,请稍后重试。",
                )
            data_item = items[0]
            if not isinstance(data_item, dict):
                raise ChannelError(
                    "Azure 响应 data[0] 不是对象",
                    retryable=True,
                    channel=_CHANNEL,
                    user_message="Azure 未返回图片,请稍后重试。",
                )
            image_content: str | None = None
            if "url" in data_item and isinstance(data_item["url"], str) and data_item["url"]:
                image_content = data_item["url"]
            elif "b64_json" in data_item and isinstance(data_item["b64_json"], str):
                image_content = f"data:image/png;base64,{data_item['b64_json']}"
            if not image_content:
                raise ChannelError(
                    "Azure 响应没有 url / b64_json",
                    retryable=True,
                    channel=_CHANNEL,
                    user_message="Azure 未返回图片,请稍后重试。",
                )
            parsed = await self._parse_image_from_content(image_content)
            if isinstance(parsed, int):
                raise ChannelError(
                    f"Azure 图片解码失败，错误码: {parsed}",
                    retryable=False,
                    channel=_CHANNEL,
                    user_message="Azure 图片已生成但解码失败。",
                )
            return parsed
        except ChannelError:
            raise
        except Exception as exc:
            # 厂商 JSON 形状不稳定,解析失败翻成通道错误。
            logger.error(f"[AzureImage] 解析 images 响应失败: {exc}")
            raise ChannelError(
                f"Azure 解析图片响应失败: {exc}",
                retryable=True,
                channel=_CHANNEL,
                user_message="Azure 生图失败,请稍后重试。",
            ) from exc


__all__ = [
    "AzureGPTImage2API",
    "audit_key_prefix",
    "azure_http_error",
    "credentials_ready",
    "is_deployment_enabled",
    "max_concurrency",
    "wire_api_version",
]
