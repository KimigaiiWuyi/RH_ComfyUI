"""OpenAI 兼容生图供应商池:供应商总开关。"""

from __future__ import annotations

from ..enabled_list import is_vendor_enabled, vendor_disabled_reason

OPENAI_IMAGE_ENABLE_KEY = "OpenAI_Image_Enable"


def is_openai_image_pool_enabled() -> bool:
    return is_vendor_enabled(OPENAI_IMAGE_ENABLE_KEY)


def openai_image_pool_disabled_reason() -> str | None:
    if is_openai_image_pool_enabled():
        return None
    return vendor_disabled_reason("OpenAI 兼容生图", "OpenAI 兼容生图供应商池栏")


__all__ = [
    "OPENAI_IMAGE_ENABLE_KEY",
    "is_openai_image_pool_enabled",
    "openai_image_pool_disabled_reason",
]
