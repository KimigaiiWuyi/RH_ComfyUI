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


__all__ = [
    "resize_long_edge",
    "correct_orientation",
    "build_process_pipeline",
    "preprocess_for_video",
]
