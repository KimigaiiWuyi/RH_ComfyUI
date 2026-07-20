"""Seedream 5.0 (Lite / Pro) ARK 生图 mapper

负责把 GenerationRequest 翻译成火山方舟 Seedream 接口的请求体:
- prompt + model 由 NodeDef.backend_model 注入
- 参考图(`request.images` bytes 列表)按 ARK 协议编码为 `data:image/<fmt>;base64,...` 数组
- size 由 NodeDef 端口的 `size_mode` 档位 + `ratio` 自然语言 hint 决定
  (具体像素交给 ARK 在文档「方式 1」下自主判断;Mapper 不在前置做像素积校验,
  该校验在 Seedream5ProImageModel.validate() 中覆盖)
- 始终 `response_format=url` + 自下载转 PNG bytes(Lite/Pro 的最大输出都是几 MB,
  b64 在大图时流量翻倍;URL 由 SDK 内部持有,统一在 mapper 内消化)
"""

from __future__ import annotations

import io
import base64
from typing import TYPE_CHECKING

from ..core.types import NodeOutput

if TYPE_CHECKING:
    from ..backends.seedream.api import SeedreamAPI


# Lite / Pro 共享一个 mapper;差异由 NodeDef 端口与 request.params 透传
def _infer_image_mime(image_bytes: bytes) -> str:
    """根据字节流魔数猜后端要求的 mime(ARK image 字段仅接受 png/jpeg/webp 等)

    默认 jpeg,仅在能识别为 png 时切换。其它格式(HEIC 等)统一走 jpeg fallback,
    由 ARK 自行报错给上游排查(参考文档:传入单图格式 jpeg/png/webp/bmp/tiff/gif/heic/heif)。
    """
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8"):
        return "image/jpeg"
    return "image/jpeg"


def _encode_images_to_data_urls(images: list[bytes]) -> list[str]:
    """bytes 列表 → data URL 列表(ARK image 字段只吃 string / string[] of URL/base64)"""
    urls: list[str] = []
    for img_bytes in images:
        if not img_bytes:
            continue
        mime = _infer_image_mime(img_bytes)
        b64 = base64.b64encode(img_bytes).decode("ascii")
        urls.append(f"data:{mime};base64,{b64}")
    return urls


def _build_size(size_mode: str | None, ratio: str | None) -> str:
    """组合 ARK 的 size 字段

    - 用户只传 size_mode → 返回档位字符串("2K" / "3K" / "4K" / "1K"),
      ARK 自行按 prompt 中的宽高比/形状描述选择像素(文档「方式 1」)
    - 用户传了 ratio 但未指定 size_mode → 默认 "2K"
    - 都没传 → "2K"(Pro/Lite 共同默认)
    """
    if size_mode:
        return size_mode
    return "2K"


def _append_ratio_hint(prompt: str, ratio: str | None) -> str:
    """比例 hint 拼到 prompt 里(ARK「方式 1」要求自然语言描述)"""
    if not ratio:
        return prompt
    # 简单追加,不覆盖用户原意
    suffix_map = {
        "1:1": "正方形构图",
        "16:9": "横向 16:9 构图",
        "9:16": "竖向 9:16 构图",
        "4:3": "横向 4:3 构图",
        "3:4": "竖向 3:4 构图",
        "3:2": "横向 3:2 构图",
        "2:3": "竖向 2:3 构图",
        "21:9": "超宽 21:9 构图",
    }
    hint = suffix_map.get(ratio)
    if not hint:
        return prompt
    # 避免重复追加
    if hint in prompt:
        return prompt
    return f"{prompt},{hint}"


async def seedream_mapper(request, api: "SeedreamAPI") -> NodeOutput:
    """Seedream 5.0 Lite / Pro 的统一 mapper

    request.params 中由 NodeDef 注入:
      - model:        后端模型 ID(来自 NodeDef.backend_model)
      - size_mode:    分辨率档位("1K" / "2K" / "3K" / "4K",Lite 支持 2K/3K/4K,Pro 支持 1K/2K)
      - ratio:        宽高比(可选;拼到 prompt)
      - output_format: 输出格式("png" / "jpeg",默认 png)
      - response_format: "url"(默认)/ "b64_json"
      - watermark:    是否带水印(默认 False)
    """
    model = request.params.get("model") or ""
    if not model:
        raise RuntimeError("Seedream 节点未声明 backend_model")

    size_mode = request.params.get("size_mode")
    ratio = request.params.get("ratio") or request.ratio or None
    output_format = request.params.get("output_format") or "png"
    response_format = request.params.get("response_format") or "url"
    watermark = bool(request.params.get("watermark", False))

    body: dict = {
        "model": model,
        "prompt": _append_ratio_hint(request.prompt, ratio),
        "size": _build_size(size_mode, ratio),
        "response_format": response_format,
        "output_format": output_format,
        "watermark": watermark,
    }

    # 参考图:0 张=文生图;1+ 张=图生图/编辑/多参考
    if request.images:
        body["image"] = _encode_images_to_data_urls(list(request.images))

    # Lite-only 字段(Pro 不接受;Pro 的 NodeDef 不暴露这些端口,
    # 但 mapper 防御性判断 —— 若 params 显式带了就报错)
    if request.params.get("seedream_tier") == "pro":
        for pro_blocked in ("sequential_image_generation", "sequential_image_generation_options", "tools"):
            if pro_blocked in request.params:
                raise RuntimeError(
                    f"Seedream 5.0 Pro 不支持字段 {pro_blocked},请改用 Seedream 5.0 Lite"
                )
    else:
        # Lite 默认走 disabled(单图);调用方可通过 params["sequential_image_generation"] = "auto" 显式开启
        seq = request.params.get("sequential_image_generation")
        if seq in ("auto", "disabled"):
            body["sequential_image_generation"] = seq
        max_imgs = request.params.get("max_images")
        if isinstance(max_imgs, int) and 1 <= max_imgs <= 15:
            body["sequential_image_generation_options"] = {"max_images": max_imgs}

    # 提示词优化(standard/fast);Lite 当前 fast 模式不支持,后端会报错,这里无脑透传由上游兜底
    opt_mode = request.params.get("optimize_mode")
    if opt_mode in ("standard", "fast"):
        body["optimize_prompt_options"] = {"mode": opt_mode}

    # 种子(若调用方显式提供)
    if request.seed is not None:
        body["seed"] = int(request.seed)

    result = await api.generate(body)

    pil_image = result["image"]
    buf = io.BytesIO()
    # 输出 mime 与 output_format 对齐(ARK 返回 PNG/JPEG;这里按 output_format 编码)
    save_format = "PNG" if (output_format or "").lower() == "png" else "JPEG"
    pil_image.save(buf, format=save_format)
    data = buf.getvalue()
    mime = "image/png" if save_format == "PNG" else "image/jpeg"

    return NodeOutput(
        status="ok",
        output_type="image",
        data=data,
        mime_type=mime,
        outputs={"image": data},
        usage={
            "model": result["model"],
            "size": result["size"],
            "generated_images": result["generated_images"],
            "output_format": result["output_format"] or output_format,
            "vendor": "ark",
        },
        raw=result["raw"],
    )
