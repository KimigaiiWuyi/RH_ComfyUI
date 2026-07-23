"""图片预处理工具 — 统一封装对图片的各种变换操作。

设计原则:
- 单一职责:每个函数做一件事,组合使用
- 链式友好:输入 bytes 输出 bytes,方便串联
- 安全降级:异常时返回原始数据,不中断流程
- 惰性导入:避免在模块加载时强依赖 PIL

当前内置处理:
- resize_long_edge: 等比缩放最长边到指定阈值

未来可扩展:
- 格式转换 / 色彩空间归一化
- 水印 / 去噪 / 超分
- EXIF 方向校正
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, Callable

# ── 核心函数 ──────────────────────────────────────────────────────


def resize_long_edge(data: bytes, max_long_edge: int = 800) -> bytes:
    """对图片进行等比缩放,最长边不超过 max_long_edge 像素。

    如果图片本身已经满足条件,直接返回原始数据不处理。
    输出统一为 PNG 格式。

    Args:
        data: 原始图片字节
        max_long_edge: 最长边上限(px),默认 800

    Returns:
        处理后的图片字节;异常时返回原始数据
    """
    try:
        from PIL import Image
    except ImportError:
        return data

    try:
        img = Image.open(BytesIO(data))
    except Exception:
        return data

    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= max_long_edge:
        return data

    scale = max_long_edge / long_edge
    new_w = int(w * scale)
    new_h = int(h * scale)

    # 兼容新旧 Pillow: 新版为 Image.Resampling.LANCZOS, 旧版为 Image.LANCZOS
    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    resized = img.resize((new_w, new_h), resampling)
    buf = BytesIO()
    resized.save(buf, format="PNG")
    return buf.getvalue()


def correct_orientation(data: bytes) -> bytes:
    """根据 EXIF Orientation 标签旋转图片到正确方向。

    处理后的图片将去除 EXIF Orientation 信息(已应用)。
    如果图片不含 EXIF 或方向为 1(正常),直接返回原始数据。

    Args:
        data: 原始图片字节

    Returns:
        校正后的图片字节;异常时返回原始数据
    """
    try:
        from PIL import Image, ExifTags
    except ImportError:
        return data

    try:
        img = Image.open(BytesIO(data))
    except Exception:
        return data

    try:
        exif = img.getexif()
        if not exif:
            return data

        orientation_key = None
        for k, v in ExifTags.TAGS.items():
            if v == "Orientation":
                orientation_key = k
                break

        if orientation_key is None or orientation_key not in exif:
            return data

        orientation = exif[orientation_key]
        transforms: dict[int, Callable[[Any], Any]] = {
            3: lambda i: i.rotate(180, expand=True),
            6: lambda i: i.rotate(270, expand=True),
            8: lambda i: i.rotate(90, expand=True),
        }

        if orientation in transforms:
            img = transforms[orientation](img)
    except Exception:
        return data

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── 管道组合 ───────────────────────────────────────────────────────


def build_process_pipeline(
    *steps: Callable[[bytes], bytes],
) -> Callable[[bytes], bytes]:
    """构建图片处理管道,按顺序执行多个处理步骤。

    Args:
        *steps: 处理函数列表,每个函数签名为 (bytes) -> bytes

    Returns:
        组合后的处理函数

    示例::

        pipeline = build_process_pipeline(
            lambda d: resize_long_edge(d, max_long_edge=800),
            correct_orientation,
        )
        result = pipeline(image_bytes)
    """

    def _run(data: bytes) -> bytes:
        for step in steps:
            data = step(data)
        return data

    return _run


# ── 预设管道 ───────────────────────────────────────────────────────


def preprocess_for_video(data: bytes, max_long_edge: int = 800) -> bytes:
    """视频生成前的标准图片预处理管道。

    当前步骤:
    1. EXIF 方向校正
    2. 等比缩放最长边

    Args:
        data: 原始图片字节
        max_long_edge: 最长边上限(px),默认 800

    Returns:
        处理后的图片字节
    """
    pipeline = build_process_pipeline(
        correct_orientation,
        lambda d: resize_long_edge(d, max_long_edge=max_long_edge),
    )
    return pipeline(data)


def preprocess_for_camera_angle(data: bytes, max_long_edge: int = 1080) -> bytes:
    """摄像机多角度工作流(RH 2080138749291356162)的图片预处理。

    两步:
    1. 等比缩放最长边到 max_long_edge(默认 1080px)
    2. 将宽高各自向下取整到 4 的倍数(ComfyUI / RunningHub 工作流要求)

    已经满足条件的原样返回,绝不放大。输出统一为 PNG。

    Args:
        data: 原始图片字节
        max_long_edge: 最长边上限(px),默认 1080

    Returns:
        处理后的图片字节;异常时返回原始数据
    """
    try:
        from PIL import Image
    except ImportError:
        return data

    try:
        img = Image.open(BytesIO(data))
    except Exception:
        return data

    w, h = img.size
    long_edge = max(w, h)

    # 1) 等比缩放最长边
    if long_edge > max_long_edge:
        scale = max_long_edge / long_edge
        w = int(w * scale)
        h = int(h * scale)

    # 2) 取整到 4 的倍数(向下取整,绝不放大)
    new_w = w - (w % 4)
    new_h = h - (h % 4)
    # 兜底:避免取整后变成 0
    new_w = max(new_w, 4)
    new_h = max(new_h, 4)

    if new_w == w and new_h == h and long_edge <= max_long_edge:
        return data

    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    resized = img.resize((new_w, new_h), resampling)
    buf = BytesIO()
    resized.save(buf, format="PNG")
    return buf.getvalue()


# ── 像素量压缩(上传/传输前瘦身) ─────────────────────────────────

# 默认 1080P 像素量阈值(1920×1080 ≈ 207 万像素)。
# 超过此值的图片等比缩小到该范围内;480P/720P/1080P 原样保留,绝不放大。
DEFAULT_MAX_PIXELS = 1920 * 1080

# 只压缩图片类 mime;视频/音频不动。
_COMPRESSIBLE_MIMES = {"image/png", "image/jpeg", "image/webp"}

# 有损格式的质量参数:视觉质量与文件体积的平衡点。
_JPEG_QUALITY = 85
_WEBP_QUALITY = 85


def compress_to_max_pixels(
    data: bytes,
    mime: str = "image/png",
    *,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    jpeg_quality: int = _JPEG_QUALITY,
    webp_quality: int = _WEBP_QUALITY,
) -> tuple[bytes, str]:
    """按像素量(宽×高)等比压缩图片,保持原格式不变。

    超过 ``max_pixels`` 的图片等比缩小到该范围内(LANCZOS 重采样);
    不超过的原样返回,绝不放大。格式保持:PNG→PNG(optimize)、
    JPEG→JPEG(quality)、WebP→WebP(quality)。

    设计原则与模块内其他函数一致:
    - 安全降级:任何异常静默返回原始 data,不中断调用方流程
    - 惰性导入:PIL 在函数体内 import,模块加载不依赖 Pillow
    - 链式友好:bytes in → bytes out

    Args:
        data: 原始图片字节
        mime: MIME 类型,决定输出格式(仅 png/jpeg/webp 会被处理)
        max_pixels: 像素量上限(宽×高),默认 1920×1080
        jpeg_quality: JPEG 有损压缩质量(1-95),默认 85
        webp_quality: WebP 有损压缩质量(1-100),默认 85

    Returns:
        ``(compressed_bytes, info)`` 元组:
        - compressed_bytes: 压缩后的图片字节(未压缩时等于原始 data)
        - info: 一行人类可读的压缩描述(空串 = 未压缩),可直接拼进日志

    Example::

        from RH_ComfyUI.utils.image_process import compress_to_max_pixels

        compressed, info = compress_to_max_pixels(raw_bytes, "image/jpeg")
        if info:
            logger.info(f"图片压缩: {info}")
    """
    if mime not in _COMPRESSIBLE_MIMES:
        return data, ""

    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return data, ""

    try:
        img = Image.open(BytesIO(data))
        img.load()  # 强制解码,后续 resize 不会在已关闭的 buffer 上炸
    except Exception:  # noqa: BLE001 - 解码失败(损坏/非真图片),原样返回
        return data, ""

    width, height = img.size
    pixels = width * height
    if pixels <= max_pixels:
        return data, ""  # 不超过阈值,原样保留

    # 等比缩小:scale = sqrt(目标像素量 / 当前像素量),保证 w*h ≈ max_pixels
    import math

    scale = math.sqrt(max_pixels / pixels)
    new_w = max(1, int(width * scale))
    new_h = max(1, int(height * scale))

    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    img = img.resize((new_w, new_h), resampling)

    buf = BytesIO()
    try:
        if mime == "image/png":
            img.save(buf, format="PNG", optimize=True)
        elif mime == "image/jpeg":
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
        elif mime == "image/webp":
            img.save(buf, format="WEBP", quality=webp_quality)
        else:
            return data, ""
    except Exception:  # noqa: BLE001 - 编码失败,回落原图
        return data, ""

    compressed = buf.getvalue()
    if len(compressed) >= len(data):
        # 压缩后反而更大(极少见),不折腾了
        return data, ""

    info = f"{width}x{height}→{new_w}x{new_h} ({len(data) // 1024}KB→{len(compressed) // 1024}KB)"
    return compressed, info


async def compress_to_max_pixels_async(
    data: bytes,
    mime: str = "image/png",
    *,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    jpeg_quality: int = _JPEG_QUALITY,
    webp_quality: int = _WEBP_QUALITY,
) -> tuple[bytes, str]:
    """``compress_to_max_pixels`` 的异步包装:PIL 是 CPU 密集的阻塞操作,
    丢进线程池跑,不阻塞事件循环。

    签名和返回值与同步版完全一致。非图片 mime 直接短路返回(不进线程池)。
    """
    if mime not in _COMPRESSIBLE_MIMES:
        return data, ""
    import asyncio

    return await asyncio.to_thread(
        compress_to_max_pixels,
        data,
        mime,
        max_pixels=max_pixels,
        jpeg_quality=jpeg_quality,
        webp_quality=webp_quality,
    )


__all__ = [
    "resize_long_edge",
    "correct_orientation",
    "build_process_pipeline",
    "preprocess_for_video",
    "preprocess_for_camera_angle",
    "compress_to_max_pixels",
    "compress_to_max_pixels_async",
    "DEFAULT_MAX_PIXELS",
]


def _has_transparency(image: Any) -> bool:
    """判断图片是否包含透明通道(入参为 PIL.Image.Image,惰性导入故用 Any)"""
    if image.mode in ("RGBA", "LA"):
        alpha = image.getchannel("A")
        transparent_mask = alpha.point(lambda value: 255 - value)
        return transparent_mask.getbbox() is not None
    return image.mode == "P" and "transparency" in image.info


def flatten_transparent_to_white(image_bytes: bytes) -> bytes:
    """将透明图片合成到白色背景,非透明图片保持原始字节

    自 rh_generate._flatten_transparent_image_to_white 平移,
    供 core.base.image.ImageGenerationBase.normalize() 统一调用。
    """
    import io

    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes))
    if not _has_transparency(image):
        return image_bytes

    rgba_image = image.convert("RGBA")
    background = Image.new("RGBA", rgba_image.size, (255, 255, 255, 255))
    background.alpha_composite(rgba_image)

    output = io.BytesIO()
    background.convert("RGB").save(output, format="PNG")
    return output.getvalue()


__all__.append("flatten_transparent_to_white")
