"""图片预处理工具 — 统一封装对图片的各种变换操作。

设计原则:
- 单一职责:每个函数做一件事,组合使用
- 链式友好:输入 bytes 输出 bytes,方便串联
- 安全降级:异常时返回原始数据,不中断流程
- 惰性导入:避免在模块加载时强依赖 PIL

当前内置处理:
- resize_long_edge: 等比缩放最长边到指定阈值
- ensure_min_edge: 等比放大使宽、高均不少于指定阈值(Seedance 参考图 ≥300)
- crop_to_seedance_aspect: 居中裁切使宽高比落入官方 0.40~2.50(目标 0.41 / 2.49)
- prepare_seedance_image_bytes: 先放大再裁切,Seedance 2.x 提交前共用

未来可扩展:
- 格式转换 / 色彩空间归一化
- 水印 / 去噪 / 超分
- EXIF 方向校正
"""

from __future__ import annotations

from io import BytesIO
from typing import Any, Callable

# Seedance 官方硬限:参考图宽、高均须 ≥ 此像素,否则上游拒收。
SEEDANCE_IMAGE_MIN_EDGE = 300

# 官方 image_url 宽高比硬限:0.40 ≤ w/h ≤ 2.50。裁切目标取内侧,避免整数取整后仍踩线。
# 勿用 0.39:低于 0.40 会被上游 InvalidParameter 拒收。
SEEDANCE_ASPECT_MIN = 0.41
SEEDANCE_ASPECT_MAX = 2.49
SEEDANCE_ASPECT_OFFICIAL_MIN = 0.40
SEEDANCE_ASPECT_OFFICIAL_MAX = 2.50

# ── 核心函数 ──────────────────────────────────────────────────────


def _lanczos():
    """兼容新旧 Pillow 的 LANCZOS 重采样常量。"""
    from PIL import Image

    return getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)


def ensure_min_edge(
    data: bytes,
    min_edge: int = SEEDANCE_IMAGE_MIN_EDGE,
) -> tuple[bytes, str]:
    """等比放大,使宽和高均不少于 ``min_edge`` 像素。

    Seedance 上游要求参考图宽、高均 ≥ 300。不足时按较大缺口放大,绝不裁切。
    色彩空间只保留 RGB / RGBA:
    - 有透明通道 → PNG RGBA
    - 原 JPEG 无透明 → JPEG RGB
    - 其余无透明 → PNG RGB

    已满足阈值、解码失败或 Pillow 不可用时原样返回,不中断调用方。

    Returns:
        ``(bytes, info)``: info 为空串表示未改写。
    """
    if not data or min_edge <= 0:
        return data, ""

    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data, ""

    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception:  # noqa: BLE001
        return data, ""

    orig_fmt = (img.format or "").upper()
    try:
        img = ImageOps.exif_transpose(img) or img
    except Exception:  # noqa: BLE001
        pass

    width, height = img.size
    if width >= min_edge and height >= min_edge:
        return data, ""
    if width < 1 or height < 1:
        return data, ""

    scale = max(min_edge / width, min_edge / height)
    new_w = max(min_edge, int(round(width * scale)))
    new_h = max(min_edge, int(round(height * scale)))

    has_alpha = _has_transparency(img)
    try:
        if has_alpha:
            img = img.convert("RGBA")
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img = img.resize((new_w, new_h), _lanczos())
    except Exception:  # noqa: BLE001
        return data, ""

    buf = BytesIO()
    try:
        if has_alpha or img.mode == "RGBA":
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            img.save(buf, format="PNG", optimize=True)
            tag = "PNG/RGBA"
        elif orig_fmt in ("JPEG", "JPG"):
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=92, optimize=True)
            tag = "JPEG/RGB"
        else:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(buf, format="PNG", optimize=True)
            tag = "PNG/RGB"
    except Exception:  # noqa: BLE001
        return data, ""

    out = buf.getvalue()
    if not out:
        return data, ""
    info = f"{width}x{height}→{new_w}x{new_h} {tag}"
    return out, info


def crop_to_seedance_aspect(data: bytes) -> tuple[bytes, str]:
    """居中裁切,使宽高比落入官方 0.40~2.50(目标 0.41 或 2.49,取更近一侧)。

    过宽(w/h > 2.49)裁左右;过竖(w/h < 0.41)裁上下。已在区间内、解码失败
    或 Pillow 不可用时原样返回。只减较长边,短边不变。
    """
    if not data:
        return data, ""

    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data, ""

    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception:  # noqa: BLE001
        return data, ""

    orig_fmt = (img.format or "").upper()
    try:
        img = ImageOps.exif_transpose(img) or img
    except Exception:  # noqa: BLE001
        pass

    width, height = img.size
    if width < 1 or height < 1:
        return data, ""

    ar = width / height
    if SEEDANCE_ASPECT_MIN <= ar <= SEEDANCE_ASPECT_MAX:
        return data, ""

    # 区间外只可能更靠近某一端:过宽 → 2.49,过竖 → 0.41。
    if ar > SEEDANCE_ASPECT_MAX:
        target_ar = SEEDANCE_ASPECT_MAX
        new_w = max(1, min(width, int(height * target_ar)))
        while new_w > 1 and new_w / height > SEEDANCE_ASPECT_MAX:
            new_w -= 1
        new_h = height
        left = (width - new_w) // 2
        box = (left, 0, left + new_w, height)
    else:
        target_ar = SEEDANCE_ASPECT_MIN
        new_h = max(1, min(height, int(width / target_ar)))
        while new_h < height and width / new_h < SEEDANCE_ASPECT_MIN:
            new_h -= 1
        if new_h < 1:
            new_h = 1
        new_w = width
        top = (height - new_h) // 2
        box = (0, top, width, top + new_h)

    if new_w == width and new_h == height:
        return data, ""

    has_alpha = _has_transparency(img)
    try:
        img = img.crop(box)
        if has_alpha:
            img = img.convert("RGBA")
        elif img.mode != "RGB":
            img = img.convert("RGB")
    except Exception:  # noqa: BLE001
        return data, ""

    buf = BytesIO()
    try:
        if has_alpha or img.mode == "RGBA":
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            img.save(buf, format="PNG", optimize=True)
            tag = "PNG/RGBA"
        elif orig_fmt in ("JPEG", "JPG"):
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=92, optimize=True)
            tag = "JPEG/RGB"
        else:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(buf, format="PNG", optimize=True)
            tag = "PNG/RGB"
    except Exception:  # noqa: BLE001
        return data, ""

    out = buf.getvalue()
    if not out:
        return data, ""
    cw, ch = img.size
    info = f"aspect {width}x{height}({ar:.2f})→{cw}x{ch}({cw / ch:.2f}) {tag}"
    return out, info


def prepare_seedance_image_bytes(data: bytes) -> tuple[bytes, str]:
    """Seedance 参考图提交前:先等比放大到宽高均 ≥300,再裁到合法宽高比。

    任一步未改写时跳过;两步都改则拼 info。失败降级为原字节。
    """
    if not data:
        return data, ""
    out, info_scale = ensure_min_edge(data)
    out, info_crop = crop_to_seedance_aspect(out)
    # 裁切只减长边,短边应仍 ≥300;再跑一次防取整边缘。
    out, info_scale2 = ensure_min_edge(out)
    infos = [x for x in (info_scale, info_crop, info_scale2) if x]
    return out, "; ".join(infos)


def image_mime_from_bytes(data: bytes) -> str:
    """按文件头猜 PNG / JPEG mime;其它回落 image/png。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return "image/png"


async def prepare_seedance_image_ref(ref: Any) -> Any:
    """Seedance 参考图提交前改写:短边放大 + 宽高比裁切,并清掉旧 url。

    2.0 / 2.5 / Fast / Mini 共用。``asset://``、非图片、取不到字节、已满足
    宽高与比例时原样返回。调用方应在 materialize / 上传之前走本函数,避免
    http URL 把小图或超比例原图交给上游。
    """
    from .core.types import MediaKind, MediaRef
    from .video_process import ensure_media_bytes

    if not isinstance(ref, MediaRef) or ref.kind != MediaKind.IMAGE:
        return ref
    url = (ref.url or "").strip()
    if url.lower().startswith("asset://"):
        return ref
    raw = ref.data if ref.data else await ensure_media_bytes(ref)
    if not raw:
        return ref
    new_data, info = prepare_seedance_image_bytes(raw)
    if not info:
        return ref
    try:
        from gsuid_core.logger import logger

        logger.info(f"[seedance] 参考图预处理: {info}")
    except Exception:  # noqa: BLE001
        pass
    return MediaRef(
        kind=MediaKind.IMAGE,
        data=new_data,
        url=None,
        role=ref.role,
        mime_type=image_mime_from_bytes(new_data),
        filename=ref.filename,
    )


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


# ── 扩图(outpaint)尺寸规划 ─────────────────────────────────────
# RunningHub 该应用输出最长边上限 1280。原图过大或目标画布过大时,
# 先按目标画布等比缩小原图与四向 padding,生成后再由调用方按 intended 尺寸展示。

OUTPAINT_MAX_SIDE: int = 1280

# 上游工作流四向都不能为 0；为 0 的边先抬到此值，调用方回图后再裁掉。
OUTPAINT_WORKFLOW_MIN_PAD: int = 100

# 腾讯云混元 ImageOutpainting 官方 Ratio,且不得与原图宽高比相同。
TX_OUTPAINT_RATIOS: tuple[str, ...] = ("1:1", "4:3", "3:4", "16:9", "9:16")
TX_OUTPAINT_RATIO_EPS: float = 0.03


def _as_nonneg_int(value: object, default: int = 0) -> int:
    try:
        n = int(round(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(0, n)


def parse_ratio_label(label: object) -> float | None:
    """把 ``16:9`` 解析成宽/高;非法返回 None。"""
    text = str(label or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        w = float(parts[0])
        h = float(parts[1])
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return w / h


def source_matches_tx_ratio(
    src_w: int,
    src_h: int,
    ratio: str,
    *,
    eps: float = TX_OUTPAINT_RATIO_EPS,
) -> bool:
    """原图宽高比是否已经等于腾讯 Ratio(相等时上游会拒收)。"""
    target = parse_ratio_label(ratio)
    if target is None:
        return False
    w = max(1, int(src_w))
    h = max(1, int(src_h))
    return abs((w / h) - target) / target < eps


def pick_tx_outpaint_ratio(
    src_w: int,
    src_h: int,
    preferred: str | None = None,
    intended_w: int | None = None,
    intended_h: int | None = None,
    *,
    eps: float = TX_OUTPAINT_RATIO_EPS,
) -> str | None:
    """选出合法且不等于原图比例的腾讯 Ratio。

    优先用 ``preferred``;否则按 intended 画布找最接近的官方比例。
    原图已经是该比例、或 intended 仍是原图比例时返回 None(调用方应拒收)。
    """
    pref = str(preferred or "").strip()
    if pref in TX_OUTPAINT_RATIOS and not source_matches_tx_ratio(src_w, src_h, pref, eps=eps):
        return pref

    iw = int(intended_w or 0)
    ih = int(intended_h or 0)
    if iw >= 2 and ih >= 2:
        intended_ar = iw / float(ih)
        src_ar = max(1, int(src_w)) / float(max(1, int(src_h)))
        if abs(intended_ar - src_ar) / max(src_ar, 1e-6) < eps:
            return None
        best: str | None = None
        best_score = 1e18
        for label in TX_OUTPAINT_RATIOS:
            if source_matches_tx_ratio(src_w, src_h, label, eps=eps):
                continue
            ar = parse_ratio_label(label)
            if ar is None:
                continue
            score = abs(ar - intended_ar) / intended_ar
            if score < best_score:
                best_score = score
                best = label
        return best
    return None


def expand_pads_to_aspect(
    src_w: int,
    src_h: int,
    top: int,
    left: int,
    right: int,
    bottom: int,
    target_ar: float,
) -> tuple[int, int, int, int]:
    """抬升 0 边后若画布比例偏离目标,在已扩展方向补 pad,让送出画布回到目标比例。"""
    w = max(1, int(src_w))
    h = max(1, int(src_h))
    t, l, r, b = (max(0, int(top)), max(0, int(left)), max(0, int(right)), max(0, int(bottom)))
    if not (target_ar > 0):
        return t, l, r, b
    send_w = w + l + r
    send_h = h + t + b
    send_ar = send_w / float(send_h)
    if abs(send_ar - target_ar) / target_ar < 0.01:
        return t, l, r, b
    if send_ar < target_ar:
        extra = max(0.0, send_h * target_ar - send_w)
        sl = l + r
        dl = extra * (l / sl) if sl > 0 else extra / 2.0
        dr = extra - dl
        return t, int(round(l + dl)), int(round(r + dr)), b
    extra = max(0.0, send_w / target_ar - send_h)
    st = t + b
    dt = extra * (t / st) if st > 0 else extra / 2.0
    db = extra - dt
    return int(round(t + dt)), l, r, int(round(b + db))


def bump_zero_outpaint_pads(
    top: object = 0,
    left: object = 0,
    right: object = 0,
    bottom: object = 0,
    *,
    min_pad: int = OUTPAINT_WORKFLOW_MIN_PAD,
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """把为 0 的边抬到 min_pad。返回 (send, crop)，均为 (top, left, right, bottom)。"""
    floor = max(1, int(min_pad))
    user = (
        _as_nonneg_int(top),
        _as_nonneg_int(left),
        _as_nonneg_int(right),
        _as_nonneg_int(bottom),
    )
    send: list[int] = []
    crop: list[int] = []
    for value in user:
        if value <= 0:
            send.append(floor)
            crop.append(floor)
        else:
            send.append(value)
            crop.append(0)
    return (send[0], send[1], send[2], send[3]), (crop[0], crop[1], crop[2], crop[3])


def plan_outpaint_scale(
    src_w: int,
    src_h: int,
    top: object = 0,
    left: object = 0,
    right: object = 0,
    bottom: object = 0,
    *,
    max_side: int = OUTPAINT_MAX_SIDE,
) -> dict[str, int | float]:
    """按原图像素与四向扩展量规划「送给上游的图 / padding」和「intended 展示尺寸」。

    输入 top/left/right/bottom 一律按**原图像素空间**理解。
    若 (src + pad) 任一边超过 max_side,则等比缩小原图与 pad,使输出落在上限内。

    Returns:
        scale, send_w/send_h(送给上游的原图尺寸), top/left/right/bottom(送给上游的 pad),
        out_w/out_h(上游实际输出), intended_w/intended_h(调用方应展示的目标尺寸)。
    """
    src_w = max(1, int(src_w))
    src_h = max(1, int(src_h))
    cap = max(1, int(max_side))
    pad_t = _as_nonneg_int(top)
    pad_l = _as_nonneg_int(left)
    pad_r = _as_nonneg_int(right)
    pad_b = _as_nonneg_int(bottom)

    intended_w = src_w + pad_l + pad_r
    intended_h = src_h + pad_t + pad_b
    scale = min(1.0, cap / float(intended_w), cap / float(intended_h))

    if scale >= 1.0:
        return {
            "scale": 1.0,
            "send_w": src_w,
            "send_h": src_h,
            "top": pad_t,
            "left": pad_l,
            "right": pad_r,
            "bottom": pad_b,
            "out_w": intended_w,
            "out_h": intended_h,
            "intended_w": intended_w,
            "intended_h": intended_h,
        }

    send_w = max(1, int(round(src_w * scale)))
    send_h = max(1, int(round(src_h * scale)))
    send_t = max(1, int(round(pad_t * scale))) if pad_t > 0 else 0
    send_l = max(1, int(round(pad_l * scale))) if pad_l > 0 else 0
    send_r = max(1, int(round(pad_r * scale))) if pad_r > 0 else 0
    send_b = max(1, int(round(pad_b * scale))) if pad_b > 0 else 0

    def _fit(src: int, a: int, b: int) -> tuple[int, int, int]:
        while src + a + b > cap:
            if a >= b and a > 0:
                a -= 1
            elif b > 0:
                b -= 1
            elif src > 1:
                src -= 1
            else:
                break
        return src, a, b

    send_w, send_l, send_r = _fit(send_w, send_l, send_r)
    send_h, send_t, send_b = _fit(send_h, send_t, send_b)

    return {
        "scale": scale,
        "send_w": send_w,
        "send_h": send_h,
        "top": send_t,
        "left": send_l,
        "right": send_r,
        "bottom": send_b,
        "out_w": send_w + send_l + send_r,
        "out_h": send_h + send_t + send_b,
        "intended_w": intended_w,
        "intended_h": intended_h,
    }


def scale_and_crop_outpaint(
    data: bytes,
    intended_w: int,
    intended_h: int,
    *,
    pre_w: int = 0,
    pre_h: int = 0,
    crop_top: int | None = None,
    crop_left: int | None = None,
    crop_right: int | None = None,
    crop_bottom: int | None = None,
    user_top: int = 0,
    user_left: int = 0,
    user_right: int = 0,
    user_bottom: int = 0,
) -> tuple[bytes, tuple[int, int, int, int]]:
    """回图当作含四向扩展的规划画布:只裁抬升边占比,再等比缩到 intended。

    返回 (png 字节, (sx, sy, sw, sh)),坐标在回图像素空间。
    """
    iw = max(2, int(intended_w))
    ih = max(2, int(intended_h))
    floor = OUTPAINT_WORKFLOW_MIN_PAD

    def _crop(explicit: int | None, user_pad: int) -> int:
        if explicit is None:
            return floor if int(user_pad) <= 0 else 0
        return max(0, int(explicit))

    ct = _crop(crop_top, user_top)
    cl = _crop(crop_left, user_left)
    cr = _crop(crop_right, user_right)
    cb = _crop(crop_bottom, user_bottom)
    pw = max(1, int(pre_w) if pre_w else iw + cl + cr)
    ph = max(1, int(pre_h) if pre_h else ih + ct + cb)

    try:
        from PIL import Image
    except ImportError:
        return data, (0, 0, 0, 0)

    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception:  # noqa: BLE001
        return data, (0, 0, 0, 0)

    aw, ah = img.size
    if aw < 2 or ah < 2:
        return data, (0, 0, aw, ah)

    # 先不缩放：在回图里切最大 intended 比例，再等比缩到 intended。
    # 1600x1400 + 16:9 → 窗 1600x900，再缩到 intended。
    target_ar = iw / float(ih)
    actual_ar = aw / float(ah)
    sx, sy, sw, sh = 0.0, 0.0, float(aw), float(ah)

    def _split(extra: float, pad_a: int, pad_b: int) -> float:
        if extra <= 0:
            return 0.0
        a_zero = pad_a <= 0
        b_zero = pad_b <= 0
        if a_zero and not b_zero:
            return extra
        if not a_zero and b_zero:
            return 0.0
        return extra / 2.0

    if actual_ar > target_ar + 1e-6:
        sw = ah * target_ar
        sx = _split(aw - sw, int(user_left), int(user_right))
    elif actual_ar < target_ar - 1e-6:
        sh = aw / target_ar
        sy = _split(ah - sh, int(user_top), int(user_bottom))

    x1 = max(0, min(aw - 1, int(round(sx))))
    y1 = max(0, min(ah - 1, int(round(sy))))
    x2 = max(x1 + 1, min(aw, int(round(sx + sw))))
    y2 = max(y1 + 1, min(ah, int(round(sy + sh))))
    cropped = img.crop((x1, y1, x2, y2))
    cw, ch = cropped.size
    if cw < 2 or ch < 2:
        return data, (x1, y1, x2 - x1, y2 - y1)
    scale = min(iw / float(cw), ih / float(ch))
    dw = max(2, int(round(cw * scale)))
    dh = max(2, int(round(ch * scale)))
    if (cw, ch) != (dw, dh):
        resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
        cropped = cropped.resize((dw, dh), resampling)
    buf = BytesIO()
    cropped.save(buf, format="PNG")
    return buf.getvalue(), (x1, y1, x2 - x1, y2 - y1)


def crop_image_to_aspect(
    data: bytes,
    intended_w: int,
    intended_h: int,
    *,
    user_top: int = 0,
    user_left: int = 0,
    user_right: int = 0,
    user_bottom: int = 0,
    min_pad: int = OUTPAINT_WORKFLOW_MIN_PAD,
) -> tuple[bytes, tuple[int, int, int, int]]:
    """兼容入口:按抬升边占比裁,再等比缩。"""
    floor = max(1, int(min_pad))
    ct = floor if int(user_top) <= 0 else 0
    cl = floor if int(user_left) <= 0 else 0
    cr = floor if int(user_right) <= 0 else 0
    cb = floor if int(user_bottom) <= 0 else 0
    return scale_and_crop_outpaint(
        data,
        intended_w,
        intended_h,
        pre_w=int(intended_w) + cl + cr,
        pre_h=int(intended_h) + ct + cb,
        crop_top=ct,
        crop_left=cl,
        crop_right=cr,
        crop_bottom=cb,
    )


def preprocess_for_outpaint(
    data: bytes,
    top: object = 0,
    left: object = 0,
    right: object = 0,
    bottom: object = 0,
    *,
    max_side: int = OUTPAINT_MAX_SIDE,
) -> tuple[bytes, dict[str, int | float]]:
    """扩图预处理:读原图尺寸,按 max_side 规划后必要时缩小原图。

    异常或缺少 Pillow 时返回原始字节 + 未缩放 plan(调用方仍可按原 pad 提交)。
    输出统一 PNG,绝不放大原图。
    """
    fallback = plan_outpaint_scale(1, 1, top, left, right, bottom, max_side=max_side)
    try:
        from PIL import Image
    except ImportError:
        return data, fallback

    try:
        img = Image.open(BytesIO(data))
        img.load()
    except Exception:  # noqa: BLE001
        return data, fallback

    w, h = img.size
    plan = plan_outpaint_scale(w, h, top, left, right, bottom, max_side=max_side)
    send_w = int(plan["send_w"])
    send_h = int(plan["send_h"])
    if send_w == w and send_h == h:
        return data, plan

    resampling = getattr(getattr(Image, "Resampling", Image), "LANCZOS", 1)
    resized = img.resize((send_w, send_h), resampling)
    buf = BytesIO()
    resized.save(buf, format="PNG")
    return buf.getvalue(), plan


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
    "SEEDANCE_IMAGE_MIN_EDGE",
    "SEEDANCE_ASPECT_MIN",
    "SEEDANCE_ASPECT_MAX",
    "SEEDANCE_ASPECT_OFFICIAL_MIN",
    "SEEDANCE_ASPECT_OFFICIAL_MAX",
    "ensure_min_edge",
    "crop_to_seedance_aspect",
    "prepare_seedance_image_bytes",
    "image_mime_from_bytes",
    "prepare_seedance_image_ref",
    "resize_long_edge",
    "correct_orientation",
    "build_process_pipeline",
    "preprocess_for_video",
    "preprocess_for_camera_angle",
    "preprocess_for_outpaint",
    "plan_outpaint_scale",
    "bump_zero_outpaint_pads",
    "expand_pads_to_aspect",
    "crop_image_to_aspect",
    "scale_and_crop_outpaint",
    "OUTPAINT_MAX_SIDE",
    "OUTPAINT_WORKFLOW_MIN_PAD",
    "TX_OUTPAINT_RATIOS",
    "TX_OUTPAINT_RATIO_EPS",
    "parse_ratio_label",
    "source_matches_tx_ratio",
    "pick_tx_outpaint_ratio",
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
