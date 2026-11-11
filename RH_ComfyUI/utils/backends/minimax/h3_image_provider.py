"""MiniMax H3-Image 官方 Provider — 同步图片生成接口(非轮询)

- 文生图:  POST {base}/v1/images/generations
- 图片编辑:POST {base}/v1/images/edits   (images 1~16,每张 128~8192px/≤4:1)
模型 ID: ``MiniMax-H3-Image``(标准) / ``MiniMax-H3-Image-Highspeed``(高速),
由 params['model_variant'] 切换,ChannelBinding.vendor_model 兜底。

同步长连返回(服务端最长 ~600s);接口非幂等,网络抖动**不重发**
(retry_attempts=1),交由上层通道/负载均衡决定是否换源,避免重复计费。
错误体为 OpenAI 风格 {type,error{type,message,http_code}};
成功响应 data[] 逐项带 url 或 per-image error。
"""

from __future__ import annotations

import base64
import asyncio
from typing import TYPE_CHECKING, Any, Optional
from dataclasses import field, dataclass

import httpx

if TYPE_CHECKING:
    from ....core.channels.failure import Stage

from gsuid_core.logger import logger

from ..http_retry import RetryingAsyncClient, download_with_network_retry
from ...core.types import MediaRef, MediaKind, ContentItemType
from ...core.request import GenerationRequest
from ...core.safe_json import dump_body, mask_body
from ..seedance._debug import mask_headers
from ..seedance.provider import DryRunInterrupt, SeedanceProviderError
from ....core.base.errors import ValidationError


class MiniMaxH3ImageProviderError(SeedanceProviderError):
    """MiniMax H3-Image 供应商错误。"""


@dataclass
class H3ImageResult:
    """一次同步生成的归一化结果。"""

    data: bytes
    mime_type: str
    vendor_model: str
    usage: dict[str, Any] = field(default_factory=dict)
    request_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _field_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in ("null", "none"):
        return None
    return text


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _pick_oai_message(resp_json: dict[str, Any], *, http_status: Optional[int] = None) -> str:
    err = _as_dict(resp_json["error"] if "error" in resp_json else None)
    candidates = (
        err["message"] if "message" in err else None,
        resp_json["message"] if "message" in resp_json else None,
        resp_json["msg"] if "msg" in resp_json else None,
    )
    for value in candidates:
        text = _field_text(value)
        if text is not None:
            return text
    if http_status is not None:
        return str(http_status)
    return "上游错误"


def _bool_param(request: GenerationRequest, key: str, default: bool, *, label: str) -> bool:
    if key not in request.params or request.params[key] is None:
        return default
    raw: object = request.params[key]
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    raise ValidationError(f"{label} 的 {key} 必须是布尔值,实际收到 {raw!r}")


def _text_param(request: GenerationRequest, key: str, *, label: str) -> str:
    if key not in request.params:
        return ""
    raw: object = request.params[key]
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValidationError(f"{label} 的 {key} 必须是字符串,实际收到 {type(raw).__name__}")
    return raw.strip()


def _int_param(request: GenerationRequest, key: str, *, label: str) -> Optional[int]:
    if key not in request.params or request.params[key] is None:
        return None
    raw: object = request.params[key]
    if isinstance(raw, bool):
        raise ValidationError(f"{label} 的 {key} 必须是整数,实际收到 {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    raise ValidationError(f"{label} 的 {key} 必须是整数,实际收到 {raw!r}")


def _parse_size(text: str, *, label: str) -> tuple[int, int]:
    """'2048x2048' → (w,h);非法抛 ValidationError。"""
    parts = text.lower().split("x")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise ValidationError(f"{label} 的 size 必须是 '宽x高' 像素串,实际收到 {text!r}")
    return int(parts[0]), int(parts[1])


class MiniMaxH3ImageProvider:
    """官方 MiniMax H3-Image 单一供应商(同步 POST,无任务态)。"""

    name = "minimax-h3-image"
    DEFAULT_BASE_URL = "https://api.minimaxi.com"
    VENDOR_MODEL = "MiniMax-H3-Image"
    VENDOR_MODEL_HIGHSPEED = "MiniMax-H3-Image-Highspeed"

    CREATE_T2I_PATH = "/v1/images/generations"
    CREATE_EDIT_PATH = "/v1/images/edits"

    MAX_IMAGES = 16
    MAX_PROMPT_CHARS = 32000
    # 上游约束:宽高比 1:3~3:1;像素面积 655360~20,000,000
    RATIO_MIN = 1.0 / 3.0
    RATIO_MAX = 3.0
    AREA_MIN = 655_360
    AREA_MAX = 20_000_000
    OUTPUT_FORMATS = ("png", "jpeg", "jpg", "webp")
    SIZE_TIERS = ("1K", "2K", "4K")
    media_prep_concurrency = 5

    def __init__(
        self,
        api_key: str = "",
        base_url: Optional[str] = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.dry_run = dry_run
        self._client: Optional[httpx.AsyncClient] = None

    def update_credentials(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        *,
        dry_run: Optional[bool] = None,
    ) -> None:
        self.api_key = api_key
        if base_url:
            self.base_url = base_url.rstrip("/")
        if dry_run is not None:
            self.dry_run = dry_run
        self._client = None

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    # ── 参数解析(纯计算) ─────────────────────────────────────

    def _label(self) -> str:
        return "MiniMax H3-Image (官方)"

    def vendor_model_of(self, request: GenerationRequest, model: Optional[str]) -> str:
        """params['model_variant'] 优先,binding.vendor_model 兜底。"""
        label = self._label()
        raw = _text_param(request, "model_variant", label=label).lower() or "standard"
        if raw in ("standard", "std", "default"):
            return self.VENDOR_MODEL
        if raw in ("highspeed", "high_speed", "fast", "turbo"):
            return self.VENDOR_MODEL_HIGHSPEED
        raise ValidationError(f"{label} 的 model_variant 非法:{raw!r};可选值:standard/highspeed")

    def _explicit_size(self, request: GenerationRequest) -> Optional[tuple[int, int]]:
        label = self._label()
        size_raw = _text_param(request, "size", label=label)
        if size_raw:
            return _parse_size(size_raw, label=label)
        w = _int_param(request, "width", label=label)
        h = _int_param(request, "height", label=label)
        if w is not None and h is not None:
            return w, h
        return None

    def _ratio(self, request: GenerationRequest) -> str:
        label = self._label()
        raw = _text_param(request, "ratio", label=label).lower()
        if not raw and isinstance(request.ratio, str):
            raw = request.ratio.strip().lower()
        if not raw or raw == "auto":
            return "auto"
        if ":" not in raw:
            raise ValidationError(f"{label} 的宽高比非法:{raw!r};须为 'auto' 或 '宽:高'")
        wr, hr = raw.split(":", 1)
        if not wr.isdigit() or not hr.isdigit() or int(wr) <= 0 or int(hr) <= 0:
            raise ValidationError(f"{label} 的宽高比非法:{raw!r}")
        r = int(wr) / int(hr)
        if r < self.RATIO_MIN - 1e-6 or r > self.RATIO_MAX + 1e-6:
            raise ValidationError(f"{label} 的宽高比 {raw!r} 超出 1:3~3:1 范围")
        return f"{int(wr)}:{int(hr)}"

    def _area(self, request: GenerationRequest) -> str:
        label = self._label()
        raw = _text_param(request, "image_size", label=label).upper() or "2K"
        if raw not in self.SIZE_TIERS:
            raise ValidationError(f"{label} 的 image_size 非法:{raw!r};可选值:{'/'.join(self.SIZE_TIERS)}")
        return raw

    def _output_format(self, request: GenerationRequest) -> str:
        label = self._label()
        raw = _text_param(request, "output_format", label=label).lower() or "jpeg"
        if raw not in self.OUTPUT_FORMATS:
            raise ValidationError(f"{label} 的 output_format 非法:{raw!r};可选值:png/jpeg/jpg/webp")
        return raw

    def _image_ref_count(self, request: GenerationRequest) -> int:
        n = len(request.images)
        if request.ordered_content:
            n = max(
                n,
                sum(
                    1
                    for item in request.ordered_content
                    if item.media is not None and item.media.kind == MediaKind.IMAGE
                ),
            )
        return n

    def validate_request(self, request: GenerationRequest) -> None:
        """发送前参数校验(零 IO);失败抛 MiniMaxH3ImageProviderError / ValidationError。"""
        label = self._label()
        prompt = (request.prompt or "").strip()
        if not prompt:
            raise MiniMaxH3ImageProviderError(
                f"{label} 必须提供非空提示词",
                code="MISSING_PROMPT",
                provider=self.name,
                user_message="请填写生图提示词。",
            )
        if len(prompt) > self.MAX_PROMPT_CHARS:
            raise MiniMaxH3ImageProviderError(
                f"提示词最长 {self.MAX_PROMPT_CHARS} 字符,当前 {len(prompt)}",
                code="PROMPT_TOO_LONG",
                provider=self.name,
                user_message=f"提示词超过 {self.MAX_PROMPT_CHARS} 字符,请缩短后重试。",
            )
        n_img = self._image_ref_count(request)
        if n_img > self.MAX_IMAGES:
            raise MiniMaxH3ImageProviderError(
                f"{label} 最多 {self.MAX_IMAGES} 张参考图,当前 {n_img}",
                code="MEDIA_OVERFLOW",
                provider=self.name,
            )
        if request.video_refs or request.audio_refs:
            raise ValidationError(f"{label} 是生图模型,不接受音视频参考素材")
        if request.ordered_content:
            for item in request.ordered_content:
                if item.media is not None and item.media.kind != MediaKind.IMAGE:
                    raise ValidationError(f"{label} 只接受图片素材,实际收到 {item.media.kind.value}")
        size = self._explicit_size(request)
        if size is not None:
            area = size[0] * size[1]
            if area < self.AREA_MIN or area > self.AREA_MAX:
                raise ValidationError(f"{label} 的 size 像素面积须为 {self.AREA_MIN}~{self.AREA_MAX}")
            r = size[0] / size[1]
            if r < self.RATIO_MIN or r > self.RATIO_MAX:
                raise ValidationError(f"{label} 的 size 宽高比须为 1:3~3:1")
        ratio = self._ratio(request)
        self._area(request)
        self._output_format(request)
        enhanced = _bool_param(request, "prompt_enhanced", True, label=label)
        if not enhanced and n_img == 0 and ratio == "auto" and size is None:
            raise ValidationError(
                f"{label} 关闭提示词增强时必须指定具体宽高比或 size 像素尺寸(auto 依赖增强器决定比例)"
            )

    # ── 媒体上传 ─────────────────────────────────────────────

    def _collect_image_refs(self, request: GenerationRequest) -> list[MediaRef]:
        """扁平 images / OC 图片项 → MediaRef 列表(保序)。"""
        refs: list[MediaRef] = []
        if request.ordered_content:
            for item in request.ordered_content:
                if item.type == ContentItemType.IMAGE and item.media is not None:
                    refs.append(item.media)
            return refs
        for blob in request.images:
            refs.append(MediaRef(kind=MediaKind.IMAGE, data=blob))
        return refs

    async def materialize_image(self, ref: MediaRef) -> str:
        """URL 透传;bytes 优先媒体 publisher 出外链,失败回落 data URI。"""
        label = self._label()
        if ref.url:
            return ref.url
        if ref.data is None:
            raise MiniMaxH3ImageProviderError(
                f"{label} 的参考图缺少内容(data/url 至少其一)",
                code="MISSING_MEDIA",
                provider=self.name,
            )
        from ...core.types import sniff_mime_for_kind_known

        mime = ref.mime_type or sniff_mime_for_kind_known(ref.data, MediaKind.IMAGE) or "image/jpeg"
        from ....core.media_host import MediaPublishError, materialize

        try:
            published = await materialize(ref.data, mime)
        except MediaPublishError as exc:
            from ....core.channels.failure import Phase, FailureKind

            raise MiniMaxH3ImageProviderError(
                f"参考图外链化失败: {exc}",
                code="MEDIA_PUBLISH_FAILED",
                kind=FailureKind.CAPABILITY,
                phase=Phase.BEFORE_SEND,
                retryable=True,
                provider=self.name,
                user_message="参考图上传失败,请稍后重试。",
            ) from exc
        if published:
            return published
        data = ref.data
        if len(data) >= 256 * 1024:
            b64_b = await asyncio.to_thread(base64.b64encode, data)
            return f"data:{mime};base64,{b64_b.decode('ascii')}"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    async def _image_urls(self, refs: list[MediaRef]) -> list[str]:
        if not refs:
            return []
        sem = asyncio.Semaphore(max(1, self.media_prep_concurrency))

        async def _one(r: MediaRef) -> str:
            async with sem:
                return await self.materialize_image(r)

        return list(await asyncio.gather(*(_one(r) for r in refs)))

    # ── 组包 / 请求 / 解析 ────────────────────────────────────

    async def render_body(
        self,
        request: GenerationRequest,
        *,
        model: Optional[str] = None,
    ) -> tuple[str, str, dict[str, str], dict[str, Any]]:
        """(method, url, headers, body);同步接口一次 POST 即结果。"""
        label = self._label()
        vendor = self.vendor_model_of(request, model)
        refs = self._collect_image_refs(request)
        urls = await self._image_urls(refs)
        is_edit = bool(urls)

        prompt = (request.prompt or "").strip()
        size = self._explicit_size(request)
        ratio = self._ratio(request)
        area = self._area(request)
        fmt = self._output_format(request)
        enhanced = _bool_param(request, "prompt_enhanced", True, label=label)

        body: dict[str, Any] = {
            "model": vendor,
            "prompt": prompt,
            "response_format": "url",
            "output_format": fmt,
        }
        if size is not None:
            body["size"] = f"{size[0]}x{size[1]}"
        else:
            if ratio != "auto" or not is_edit:
                body["aspect_ratio"] = ratio
            body["area"] = area
        if is_edit:
            body["images"] = [{"image_url": u} for u in urls]
        elif not enhanced:
            body["disable_prompt_enhanced"] = True
        compression = _int_param(request, "output_compression", label=label)
        if compression is not None:
            if not 0 <= compression <= 100:
                raise ValidationError(f"{label} 的 output_compression 须为 0~100")
            body["output_compression"] = compression
        if request.watermark or _bool_param(request, "watermark", False, label=label):
            body["aigc_watermark"] = True

        path = self.CREATE_EDIT_PATH if is_edit else self.CREATE_T2I_PATH
        url = f"{self.base_url}{path}"
        return "POST", url, self._headers(), body

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # 非幂等 POST:网络层不重发(抖动可能已建单计费);超时对齐上游 600s 上限
            self._client = RetryingAsyncClient(
                timeout=httpx.Timeout(610.0, connect=10.0),
                retry_attempts=1,
            )
        return self._client

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        json: Optional[dict[str, Any]] = None,
        stage: "Stage" = "other",
    ) -> dict[str, Any]:
        from ....rh_config.comfyui_config import plugin_dry_run

        if self.dry_run or plugin_dry_run():
            logger.info(
                f"[MiniMaxH3Image] Dry-Run 拦截(未发送) {method} {url}\n"
                f"  headers: {mask_headers(headers)}\n"
                f"  body:\n{dump_body(mask_body(json))}"
            )
            raise DryRunInterrupt(f"[MiniMaxH3Image] Dry-Run 已启用,请求未发送: {method} {url}")

        logger.debug(
            f"[MiniMaxH3Image] 请求 {method} {url}\n"
            f"  headers: {mask_headers(headers)}\n"
            f"  body:\n{dump_body(mask_body(json))}"
        )
        client = self._get_client()
        resp = await client.request(method, url, headers=headers, json=json)
        resp_json: Any = None
        try:
            resp_json = resp.json()
            resp_text = dump_body(mask_body(resp_json))
        except Exception:
            resp_text = (resp.text or "")[:2000]
        emit = logger.warning if resp.status_code >= 400 else logger.debug
        emit(f"[MiniMaxH3Image] 响应 {resp.status_code} ({len(resp.content)} bytes)\n  body: {resp_text}")
        if resp.status_code >= 400:
            raise self._build_http_error(resp, resp_json, stage=stage)
        if isinstance(resp_json, dict):
            return resp_json
        from ....core.channels.failure import FailureKind, classify_http_status

        _, bad_phase = classify_http_status(resp.status_code or 500, stage=stage)
        raise MiniMaxH3ImageProviderError(
            f"MiniMax H3-Image 返回非 JSON: {resp_text[:500]}",
            code="BAD_RESPONSE",
            kind=FailureKind.PROVIDER_FAULT,
            phase=bad_phase,
            provider=self.name,
            user_message="上游返回格式异常,请稍后重试。",
        )

    def _build_http_error(
        self, resp: httpx.Response, resp_json: Any, *, stage: "Stage" = "other"
    ) -> MiniMaxH3ImageProviderError:
        body = resp_json if isinstance(resp_json, dict) else {"raw": (resp.text or "")[:500]}
        vendor_msg = _pick_oai_message(body, http_status=resp.status_code)
        from ....core.channels.failure import classify_http_status

        kind, phase = classify_http_status(resp.status_code, stage=stage)
        retryable = False if resp.status_code in (400, 402, 422) else None
        transient = True if resp.status_code == 529 else None
        return MiniMaxH3ImageProviderError(
            f"MiniMax H3-Image API 错误 {resp.status_code}: {vendor_msg}",
            code="HTTP_ERROR",
            kind=kind,
            phase=phase,
            retryable=retryable,
            transient=transient,
            provider=self.name,
            http_status=resp.status_code,
            user_message=vendor_msg,
        )

    def _item_error(self, item: dict[str, Any]) -> str:
        err = _as_dict(item["error"] if "error" in item else None)
        code = _field_text(err["code"] if "code" in err else None)
        msg = _field_text(err["message"] if "message" in err else None) or "生成失败"
        return f"{code}: {msg}" if code else msg

    def parse_response(self, resp: dict[str, Any], vendor: str) -> tuple[str, str, dict[str, Any], str]:
        """(image_url, mime, usage, request_id);data[] 逐项成败分别计费。"""
        from ....core.channels.failure import Phase, FailureKind

        data = resp["data"] if "data" in resp else None
        if not isinstance(data, list) or not data:
            raise MiniMaxH3ImageProviderError(
                f"MiniMax H3-Image 响应缺少 data[]: {resp}",
                code="EMPTY_RESULT",
                kind=FailureKind.PROVIDER_FAULT,
                phase=Phase.ACCEPTED,
                provider=self.name,
                user_message="上游未返回图片结果,请稍后重试。",
            )
        first = _as_dict(data[0])
        url = _field_text(first["url"] if "url" in first else None)
        if url is None and "b64_json" in first:
            b64 = first["b64_json"]
            if isinstance(b64, str) and b64.strip():
                fmt = _field_text(first["output_format"] if "output_format" in first else None) or "jpeg"
                url = f"data:image/{fmt};base64,{b64.strip()}"
        if url is None:
            errors = [self._item_error(_as_dict(item)) for item in data if _as_dict(item).get("error")]
            detail = "; ".join(errors) if errors else str(resp)[:500]
            raise MiniMaxH3ImageProviderError(
                f"MiniMax H3-Image 生成失败: {detail}",
                code="TASK_FAILED",
                kind=FailureKind.TASK_FAILED,
                phase=Phase.ACCEPTED,
                provider=self.name,
                user_message=detail,
            )
        fmt = (_field_text(first["output_format"] if "output_format" in first else None) or "jpeg").lower()
        mime = "image/jpg" if fmt == "jpg" else f"image/{fmt}"
        usage = _as_dict(resp["usage"] if "usage" in resp else None)
        request_id = (
            _field_text(resp["request_id"] if "request_id" in resp else None)
            or _field_text(resp["id"] if "id" in resp else None)
            or ""
        )
        return url, mime, usage, request_id

    async def run(
        self,
        request: GenerationRequest,
        *,
        model: Optional[str] = None,
        on_progress: Optional[Any] = None,
    ) -> H3ImageResult:
        """校验 → 组包 → 单次 POST → 取图下载(同步接口无任务态)。"""
        self.validate_request(request)
        vendor = self.vendor_model_of(request, model)
        method, url, headers, body = await self.render_body(request, model=model)
        masked = mask_body(body)
        logger.info(f"[MiniMaxH3Image] 创建任务: model={vendor}, endpoint={url}\n  request:\n{dump_body(masked)}")
        from ....core.telemetry.wire_capture import set_wire_from_http_body

        set_wire_from_http_body(masked, prompt=(request.prompt or "").strip() or None)
        resp = await self._request(method, url, headers=headers, json=body, stage="create")
        image_url, mime, usage, request_id = self.parse_response(resp, vendor)

        if image_url.startswith("data:"):
            _, _, payload = image_url.partition(",")
            data = await asyncio.to_thread(base64.b64decode, payload)
        else:
            data = await download_with_network_retry(image_url, label="MiniMax H3-Image 结果图")
        if not data:
            from ....core.channels.failure import Phase, FailureKind

            raise MiniMaxH3ImageProviderError(
                "MiniMax H3-Image 结果下载为空",
                code="EMPTY_RESULT",
                kind=FailureKind.TASK_FAILED,
                phase=Phase.ACCEPTED,
                provider=self.name,
                user_message="图片已生成但下载失败,请稍后重试。",
            )
        return H3ImageResult(
            data=data,
            mime_type=mime,
            vendor_model=vendor,
            usage=usage,
            request_id=request_id,
            raw=resp if isinstance(resp, dict) else {},
        )


__all__ = [
    "H3ImageResult",
    "MiniMaxH3ImageProvider",
    "MiniMaxH3ImageProviderError",
]
