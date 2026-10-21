"""千问图像目录。名字与 DashScope 模型 ID 相同。

同步协议见百炼「图像生成与编辑」与「千问-文生图」。
2.1 / 3.0 用同一套 messages 体;2.0 不传改写模式和思考开关。
"""

from __future__ import annotations

from dataclasses import dataclass

QWEN_IMAGE_30_PRO = "qwen-image-3.0-pro"
QWEN_IMAGE_30 = "qwen-image-3.0"
QWEN_IMAGE_21_PRO = "qwen-image-2.1-pro"
QWEN_IMAGE_21_TURBO = "qwen-image-2.1-turbo"
QWEN_IMAGE_20_PRO = "qwen-image-2.0-pro"
QWEN_IMAGE_20 = "qwen-image-2.0"

# 输出面积 ≤ 此值记 1K,更大且不超过 2048² 记 2K。
TIER_1K_MAX_AREA = 2_250_000
MAX_OUTPUT_IMAGES = 6
MAX_INPUT_BYTES = 10 * 1024 * 1024
NEGATIVE_LIMIT = 500
SEED_MAX = 2_147_483_647
QWEN_IMAGE_RATIOS: tuple[str, ...] = (
    "auto",
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "21:9",
)

# 1K 面积都 ≤ 2,250,000;2K 用 2.0 文档推荐像素,其余比例落在 2K 档且 ≤ 2048²。
SIZE_1K: dict[str, str] = {
    "1:1": "1024*1024",
    "16:9": "1920*1080",
    "9:16": "1080*1920",
    "4:3": "1536*1152",
    "3:4": "1152*1536",
    "3:2": "1536*1024",
    "2:3": "1024*1536",
    "21:9": "2016*864",
}
SIZE_2K: dict[str, str] = {
    "1:1": "2048*2048",
    "16:9": "2688*1536",
    "9:16": "1536*2688",
    "4:3": "2368*1728",
    "3:4": "1728*2368",
    "3:2": "2496*1664",
    "2:3": "1664*2496",
    "21:9": "2688*1152",
}


@dataclass(frozen=True)
class QwenImageSpec:
    name: str
    display_name: str
    description: str
    priority: int
    max_images: int
    allow_negative: bool
    allow_extend_mode: bool
    point_cost: int


QWEN_IMAGE_SPECS: tuple[QwenImageSpec, ...] = (
    QwenImageSpec(
        QWEN_IMAGE_30_PRO,
        "Qwen-Image 3.0 Pro",
        "文字与编辑，2K 更贵",
        69,
        3,
        True,
        True,
        25,
    ),
    QwenImageSpec(
        QWEN_IMAGE_30,
        "Qwen-Image 3.0",
        "质量与速度均衡",
        68,
        3,
        True,
        True,
        18,
    ),
    QwenImageSpec(
        QWEN_IMAGE_21_PRO,
        "Qwen-Image 2.1 Pro",
        "最多 10 张参考图",
        67,
        10,
        False,
        True,
        25,
    ),
    QwenImageSpec(
        QWEN_IMAGE_21_TURBO,
        "Qwen-Image 2.1 Turbo",
        "性价比高，支持编辑",
        66,
        10,
        False,
        True,
        10,
    ),
    QwenImageSpec(
        QWEN_IMAGE_20_PRO,
        "Qwen-Image 2.0 Pro",
        "文字渲染强，可反向词",
        65,
        3,
        True,
        False,
        50,
    ),
    QwenImageSpec(
        QWEN_IMAGE_20,
        "Qwen-Image 2.0",
        "生成更快，可反向词",
        63,
        3,
        True,
        False,
        20,
    ),
)

QWEN_IMAGE_BY_NAME: dict[str, QwenImageSpec] = {spec.name: spec for spec in QWEN_IMAGE_SPECS}
QWEN_IMAGE_NAMES: tuple[str, ...] = tuple(spec.name for spec in QWEN_IMAGE_SPECS)


def qwen_image_spec(name: str) -> QwenImageSpec | None:
    if name in QWEN_IMAGE_BY_NAME:
        return QWEN_IMAGE_BY_NAME[name]
    return None


__all__ = [
    "MAX_INPUT_BYTES",
    "MAX_OUTPUT_IMAGES",
    "NEGATIVE_LIMIT",
    "SEED_MAX",
    "QWEN_IMAGE_20",
    "QWEN_IMAGE_20_PRO",
    "QWEN_IMAGE_21_PRO",
    "QWEN_IMAGE_21_TURBO",
    "QWEN_IMAGE_30",
    "QWEN_IMAGE_30_PRO",
    "QWEN_IMAGE_BY_NAME",
    "QWEN_IMAGE_NAMES",
    "QWEN_IMAGE_RATIOS",
    "QWEN_IMAGE_SPECS",
    "QwenImageSpec",
    "SIZE_1K",
    "SIZE_2K",
    "TIER_1K_MAX_AREA",
    "qwen_image_spec",
]
