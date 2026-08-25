"""腾讯云混元 AI 艺术 HTTP 客户端 — ImageOutpainting(TC3-HMAC,不依赖官方 SDK)

凭证每次从 SERVICE_CONFIG 读取,网页控制台改 secret 后无需重启。
"""

from __future__ import annotations

import hmac
import json
import time
import base64
import asyncio
import hashlib
from io import BytesIO
from typing import Any
from datetime import datetime, timezone

import aiohttp
from PIL import Image

from gsuid_core.logger import logger

from ..http_retry import is_network_error, call_with_network_retry
from ....rh_config.comfyui_config import SERVICE_CONFIG

SERVICE = "aiart"
DEFAULT_HOST = "aiart.tencentcloudapi.com"
DEFAULT_REGION = "ap-guangzhou"
ACTION = "ImageOutpainting"
VERSION = "2022-12-29"
CONTENT_TYPE = "application/json; charset=utf-8"
# 上游常见上限约 6MB(base64 后);编码前先压到此体积。
MAX_INPUT_BYTES = 4_500_000
MAX_INPUT_SIDE = 4096


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sign_tc3(
    *,
    secret_id: str,
    secret_key: str,
    service: str,
    host: str,
    action: str,
    payload: str,
    timestamp: int,
    region: str,
    version: str,
) -> dict[str, str]:
    """构造 TC3-HMAC-SHA256 请求头。payload 必须是将要发送的 UTF-8 JSON 原文。"""
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    hashed_payload = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    canonical_headers = (
        f"content-type:{CONTENT_TYPE}\n"
        f"host:{host}\n"
        f"x-tc-action:{action.lower()}\n"
    )
    signed_headers = "content-type;host;x-tc-action"
    canonical_request = (
        "POST\n"
        "/\n"
        "\n"
        f"{canonical_headers}\n"
        f"{signed_headers}\n"
        f"{hashed_payload}"
    )
    credential_scope = f"{date}/{service}/tc3_request"
    string_to_sign = (
        "TC3-HMAC-SHA256\n"
        f"{timestamp}\n"
        f"{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )
    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac_sha256(secret_date, service)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"TC3-HMAC-SHA256 Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "Content-Type": CONTENT_TYPE,
        "Host": host,
        "X-TC-Action": action,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": version,
        "X-TC-Region": region,
    }


def _strip_data_url(raw: str) -> str:
    text = raw.strip()
    if "base64," in text[:80]:
        return text.split("base64,", 1)[1]
    return text


def encode_input_image(data: bytes) -> str:
    """把输入图压到上游可接受的体积,返回无前缀 base64。"""
    if not data:
        raise RuntimeError("扩图缺少输入图")
    img = Image.open(BytesIO(data))
    img.load()
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")

    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    w, h = img.size
    longest = max(w, h)
    if longest > MAX_INPUT_SIDE:
        scale = MAX_INPUT_SIDE / float(longest)
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        img = img.resize((nw, nh), resample)

    def _dump(im: Image.Image, fmt: str, **kwargs: Any) -> bytes:
        buf = BytesIO()
        im.save(buf, format=fmt, **kwargs)
        return buf.getvalue()

    raw = _dump(img, "PNG")
    if len(raw) > MAX_INPUT_BYTES:
        rgb = img.convert("RGB")
        quality = 90
        raw = _dump(rgb, "JPEG", quality=quality, optimize=True)
        while len(raw) > MAX_INPUT_BYTES and quality > 50:
            quality -= 10
            raw = _dump(rgb, "JPEG", quality=quality, optimize=True)
        while len(raw) > MAX_INPUT_BYTES and max(rgb.size) > 512:
            nw = max(1, rgb.size[0] // 2)
            nh = max(1, rgb.size[1] // 2)
            rgb = rgb.resize((nw, nh), resample)
            raw = _dump(rgb, "JPEG", quality=max(quality, 70), optimize=True)
    if len(raw) > MAX_INPUT_BYTES:
        raise RuntimeError("扩图输入图过大,压缩后仍超过上游限制")
    return base64.b64encode(raw).decode("ascii")


def decode_result_image(raw: str) -> bytes:
    """把上游 ResultImage(base64 或 data URL)解成 PNG 字节。"""
    payload = _strip_data_url(raw)
    try:
        binary = base64.b64decode(payload, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"扩图返回无法解码: {exc}") from exc
    if not binary:
        raise RuntimeError("扩图返回空图")
    try:
        im = Image.open(BytesIO(binary))
        im.load()
        out = BytesIO()
        im.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return binary


class TxAiartAPI:
    """混元 AI 艺术客户端。凭证用 @property 每次读配置。"""

    @property
    def secret_id(self) -> str:
        return (SERVICE_CONFIG.get_config("TX_AIArt_secret_id").data or "").strip()

    @property
    def secret_key(self) -> str:
        return (SERVICE_CONFIG.get_config("TX_AIArt_secret_key").data or "").strip()

    @property
    def region(self) -> str:
        return (SERVICE_CONFIG.get_config("TX_AIArt_region").data or DEFAULT_REGION).strip() or DEFAULT_REGION

    @property
    def host(self) -> str:
        return DEFAULT_HOST

    def configured(self) -> bool:
        return bool(self.secret_id and self.secret_key)

    def require_credentials(self) -> tuple[str, str]:
        sid, skey = self.secret_id, self.secret_key
        if not sid or not skey:
            raise RuntimeError(
                "未配置腾讯云混元扩图凭证,请在 Web 控制台填写 TX_AIArt_secret_id / TX_AIArt_secret_key"
            )
        return sid, skey

    async def image_outpainting(
        self,
        image: bytes,
        ratio: str,
        *,
        max_retries: int = 5,
    ) -> bytes:
        sid, skey = self.require_credentials()
        encoded = await asyncio.to_thread(encode_input_image, image)
        body_obj = {
            "Ratio": ratio,
            "InputImage": encoded,
            "RspImgType": "base64",
            "LogoAdd": 0,
        }
        payload = json.dumps(body_obj, ensure_ascii=False, separators=(",", ":"))
        url = f"https://{self.host}"
        last_error = "未知错误"

        for attempt in range(1, max_retries + 1):
            ts = int(time.time())
            headers = sign_tc3(
                secret_id=sid,
                secret_key=skey,
                service=SERVICE,
                host=self.host,
                action=ACTION,
                payload=payload,
                timestamp=ts,
                region=self.region,
                version=VERSION,
            )
            logger.info(f"[tx_aiart] ImageOutpainting ratio={ratio} attempt={attempt}/{max_retries}")
            try:
                timeout = aiohttp.ClientTimeout(total=120)

                async def _once() -> tuple[int, str]:
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.post(url, data=payload.encode("utf-8"), headers=headers) as resp:
                            return resp.status, await resp.text()

                status, text = await call_with_network_retry(_once, label=f"POST {url}")
                try:
                    data = json.loads(text)
                except Exception:
                    last_error = f"HTTP {status}: {text[:240]}"
                    logger.warning(f"[tx_aiart] 非 JSON 响应: {last_error}")
                    await asyncio.sleep(5.0)
                    continue
            except Exception as exc:  # noqa: BLE001
                if is_network_error(exc):
                    last_error = str(exc)
                    logger.warning(f"[tx_aiart] 请求异常(网络重试已耗尽): {exc}")
                    raise RuntimeError(f"腾讯云扩图失败: {last_error}") from exc
                last_error = str(exc)
                logger.warning(f"[tx_aiart] 请求异常: {exc}")
                await asyncio.sleep(5.0)
                continue

            response = data.get("Response") if isinstance(data, dict) else None
            if not isinstance(response, dict):
                last_error = f"响应缺少 Response: {str(data)[:240]}"
                await asyncio.sleep(1.5)
                continue
            err = response.get("Error")
            if err:
                code = err.get("Code") if isinstance(err, dict) else ""
                msg = err.get("Message") if isinstance(err, dict) else str(err)
                last_error = f"{code}: {msg}" if code else str(msg)
                logger.warning(f"[tx_aiart] 业务错误: {last_error}")
                await asyncio.sleep(1.5)
                continue
            result = response.get("ResultImage")
            if not result or not isinstance(result, str):
                last_error = "响应缺少 ResultImage"
                await asyncio.sleep(1.5)
                continue
            return await asyncio.to_thread(decode_result_image, result)

        raise RuntimeError(f"腾讯云扩图失败: {last_error}")


tx_aiart_api = TxAiartAPI()
