"""Seedream API 客户端 — 封装火山方舟 ARK `POST /images/generations`

凭证读取遵循文档 §11.2.A 范式:`@property` 直读 SERVICE_CONFIG,
中途改 Seedance_apikey_ark / Seedance_BaseURL_ark 不重启即生效。

要点:
- 空 key 不拼 Bearer(§11.3 红线 1,防 `LocalProtocolError: Illegal header value b'Bearer '`)
- base_url 变更后 `images_url` 必须跟着重算(§11.3 红线 3)
- 响应 `response_format` 支持 url / b64_json,本客户端统一走 url,再 httpx 下载转 PIL.Image
- 参考图(bytes 列表)在 mapper 阶段就编码成 `data:image/<fmt>;base64,...` 字符串数组传入
"""

from __future__ import annotations

import io
import re
import base64
import asyncio
from typing import Any, Dict, List, Union, Optional

import aiohttp
from PIL import Image

from gsuid_core.logger import logger

from ..http_retry import (
    is_network_error,
    call_with_network_retry,
    download_with_network_retry,
)
from ....rh_config.comfyui_config import SERVICE_CONFIG


class SeedreamAPI:
    """Seedream 5.0 (Lite / Pro) ARK 图片生成客户端"""

    DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"

    def __init__(self) -> None:
        # 不在 __init__ 里读配置 —— 全交给 @property,避免 key 为空时被冻住
        self._base_url_override: Optional[str] = None

    # ── 凭证(读 Seedance 面板,同源 ARK) ──────────────────────

    @property
    def api_key(self) -> str:
        """Seedream 复用火山 ARK Seedance 同 Key,无需单独配置"""
        key = SERVICE_CONFIG.get_config("Seedance_apikey_ark").data
        return key or ""

    @property
    def base_url(self) -> str:
        url = self._base_url_override or SERVICE_CONFIG.get_config("Seedance_BaseURL_ark").data
        return (url or self.DEFAULT_BASE_URL).rstrip("/")

    @property
    def images_url(self) -> str:
        return f"{self.base_url}/images/generations"

    @property
    def enabled(self) -> bool:
        """Seedance 启用开关同时控制 Seedream"""
        try:
            return bool(SERVICE_CONFIG.get_config("Seedance_Enable_ark").data)
        except Exception:
            return False

    def refresh_config(self) -> None:
        """executor 入口每次调一次,清掉派生 URL 缓存

        base_url 在配置里被改了之后,images_url 必须跟着重算。
        """
        self._base_url_override = None

    # ── 请求 ───────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        h: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:  # §11.3 红线 1:空 key 不拼 Bearer
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _require_api_key(self) -> str:
        key = self.api_key
        if not key:
            raise RuntimeError("[Seedream] 未配置火山 ARK API Key,请在 Web 控制台配置 Seedance_apikey_ark 后重试")
        return key

    async def _request(
        self,
        body: Dict[str, Any],
        *,
        max_retries: int = 3,
    ) -> Union[Dict[str, Any], int]:
        """POST {images_url} 并按业务/网络状态返回 JSON 或错误码"""
        url = self.images_url
        logger.info(f"[Seedream] 请求: POST {url} model={body.get('model')}")

        for attempt in range(max_retries):
            try:
                self._require_api_key()

                async def _once() -> Union[Dict[str, Any], int]:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, headers=self._headers(), json=body) as resp:
                            logger.info(f"[Seedream] 响应状态: {resp.status}")
                            if resp.status != 200:
                                try:
                                    err_body = await resp.text()
                                    logger.warning(f"[Seedream] 错误响应体: {err_body[:500]}")
                                except Exception:
                                    pass
                                return resp.status
                            data = await resp.json()
                            logger.debug(f"[Seedream] 响应数据: {data}")
                            return data

                result = await call_with_network_retry(_once, label=f"POST {url}")
                if isinstance(result, int):
                    if result in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                        logger.warning(f"[Seedream] {result} 退避后重试 ({attempt + 1}/{max_retries})")
                        continue
                    return result
                return result
            except Exception as e:  # noqa: BLE001
                if is_network_error(e):
                    logger.warning(f"[Seedream] 请求异常(网络重试已耗尽): {e}")
                    return 500
                logger.warning(f"[Seedream] 请求异常: {e}")
                if attempt < max_retries - 1:
                    continue
                return 500
        return 500

    # ── 图片解码工具 ──────────────────────────────────────────

    async def _download_image_from_url(self, url: str) -> Union[Image.Image, int]:
        logger.info(f"[Seedream] 下载图片: {url}")
        try:
            raw = await download_with_network_retry(url, timeout=120.0, label="Seedream")
            return Image.open(io.BytesIO(raw))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Seedream] 下载图片失败: {e}")
            return 500

    @staticmethod
    def _decode_base64_image(b64: str) -> Union[Image.Image, int]:
        try:
            if b64.startswith("data:"):
                m = re.match(r"data:image/([a-zA-Z+]+);base64,(.+)", b64)
                if m:
                    b64 = m.group(2)
            return Image.open(io.BytesIO(base64.b64decode(b64)))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[Seedream] 解码 base64 图片失败: {e}")
            return 500

    async def _parse_image_from_content(self, content: str) -> Union[Image.Image, int]:
        content = content.strip()
        if content.startswith(("http://", "https://")):
            return await self._download_image_from_url(content)
        # 否则按 base64 解码
        return self._decode_base64_image(content)

    # ── 公开入口 ─────────────────────────────────────────────

    async def generate(self, body: Dict[str, Any], *, decode_all: bool = False) -> Dict[str, Any]:
        """发请求并把 data[] 的 url / b64_json 解码为 PIL.Image

        ``decode_all=False``(默认):只校验并解码 data[0]——单图及 5.0 连续组图
        沿用首图语义,其余条目不进 ``images`` 也不下载,后续条目失败不影响首图。
        ``decode_all=True``(5.5 图层拆分):全部条目并发解码,任一失败整体报错。

        返回结构:
          {
              "image": PIL.Image,            # 第一张(单图 / 图层底图 z_index=0)
              "images": [PIL.Image, ...],    # 已解码条目,与 items 前缀同序
              "items": [dict, ...],          # 原始 data 条目(z_index/bounding_box/OCR 等)
              "model": str,                  # 实际响应的 model 字段
              "size": str,                   # 首条 size(若有)
              "output_format": str,          # 首条 output_format(若有)
              "generated_images": int,       # usage.generated_images(若有)
              "input_images": int,           # usage.input_images(若有,5.5)
              "raw": dict,                   # 厂商原始响应,落统计/审计
          }

        出错抛 RuntimeError,带 HTTP 状态码/Ark 业务码,供 AdapterChannel 二次翻译为 ChannelError。
        """
        resp = await self._request(body)
        if isinstance(resp, int):
            raise RuntimeError(f"[Seedream] 生成失败,HTTP 状态码: {resp}")

        # 顶层 error(整请求失败)
        top_err = resp.get("error")
        if isinstance(top_err, dict):
            code = top_err.get("code") or "ARK_ERROR"
            msg = top_err.get("message") or "上游错误"
            raise RuntimeError(f"[Seedream] {code}: {msg}")

        data_list = resp.get("data")
        if not isinstance(data_list, list) or not data_list:
            raise RuntimeError(f"[Seedream] 响应缺少 data: {resp}")

        if decode_all:
            # 图层拆分任一图层失败整体报错;组图失败项也带 error
            for item in data_list:
                if isinstance(item, dict) and isinstance(item.get("error"), dict):
                    err = item["error"]
                    raise RuntimeError(f"[Seedream] 单图错误 {err.get('code', '')}: {err.get('message', '生成失败')}")
            images = await self._decode_all(data_list)
        else:
            first_raw = data_list[0]
            if isinstance(first_raw, dict) and isinstance(first_raw.get("error"), dict):
                err = first_raw["error"]
                raise RuntimeError(f"[Seedream] 单图错误 {err.get('code', '')}: {err.get('message', '生成失败')}")
            images = [await self._decode_one(first_raw, 0)]

        first = data_list[0] if isinstance(data_list[0], dict) else {}
        usage = resp.get("usage") or {}
        return {
            "image": images[0],
            "images": images,
            "items": data_list,
            "model": resp.get("model") or body.get("model", ""),
            "size": first.get("size") or "",
            "output_format": first.get("output_format") or body.get("output_format", ""),
            "generated_images": int(usage.get("generated_images") or len(images)),
            "input_images": int(usage.get("input_images") or 0),
            "raw": resp,
        }

    async def _decode_one(self, item: Any, index: int) -> Image.Image:
        if not isinstance(item, dict):
            raise RuntimeError(f"[Seedream] 第 {index} 条响应不是对象: {item!r}")
        url_or_b64 = item.get("url") or (f"data:image/png;base64,{item['b64_json']}" if "b64_json" in item else None)
        if not url_or_b64:
            raise RuntimeError(f"[Seedream] 第 {index} 条响应缺少 url/b64_json: {item}")
        img = await self._parse_image_from_content(url_or_b64)
        if isinstance(img, int):
            raise RuntimeError(f"[Seedream] 第 {index} 条图片解码失败,错误码: {img}")
        return img

    async def _decode_all(self, data_list: List[Any]) -> List[Image.Image]:
        """并发解码全部 data 条目(图层拆分最多 ~100 张);任一失败整体报错。"""
        semaphore = asyncio.Semaphore(8)

        async def _one(item: Any, index: int) -> Image.Image:
            async with semaphore:
                return await self._decode_one(item, index)

        results = await asyncio.gather(*(_one(item, i) for i, item in enumerate(data_list)))
        if not results:
            raise RuntimeError("[Seedream] 未解码出任何图片")
        return list(results)


# 全局单例
seedream_api = SeedreamAPI()
