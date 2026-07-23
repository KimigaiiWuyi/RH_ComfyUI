"""媒体外链化扩展点 — bytes → 上游可 GET 的公网 URL

设计动机
--------
Seedream 等上游对 base64 body 有大小限制(超限 413),生产环境通常把参考图
上传到 R2 再传外链。R2 出口属于 ``canvas_backend`` / 业务侧能力,开源引擎
**不得**反向 import ``aigc_system`` 或 ``canvas_backend``。

本模块提供与 ``channel_registry`` 同构的扩展点:

- 无 publisher 注册 → ``materialize()`` 返回 ``None``,调用方回落 data URL
- 有 publisher 注册 → 调用 publisher;失败抛 ``MediaPublishError``
  (调用方翻成 retryable 错误以便 failover)

闭源/宿主插件在 ``@on_core_start`` 中注册:

    from RH_ComfyUI.core import set_media_publisher
    from aigc_system.aifoundation.media_host import materialize as aif_materialize

    set_media_publisher(aif_materialize)

开源仓库对外部实现零感知:只认 ``MediaPublisher`` 可调用契约。
"""

from __future__ import annotations

import threading
from typing import Awaitable, Callable, Optional

from gsuid_core.logger import logger

# (data, mime) → 公网 URL 字符串
MediaPublisher = Callable[[bytes, str], Awaitable[str]]


class MediaPublishError(RuntimeError):
    """媒体外链化失败(如对象存储不可用)。

    调用方应翻成 retryable 通道错误,让负载均衡切换到不依赖外链的供应商。
    """


class MediaHostRegistry:
    """进程级媒体 publisher 注册表;线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._publisher: Optional[MediaPublisher] = None

    def set(self, publisher: Optional[MediaPublisher]) -> None:
        with self._lock:
            self._publisher = publisher

    def get(self) -> Optional[MediaPublisher]:
        with self._lock:
            return self._publisher

    def clear(self) -> None:
        """清空(仅供测试 / 卸载)。"""
        self.set(None)


media_host_registry = MediaHostRegistry()


def set_media_publisher(publisher: Optional[MediaPublisher]) -> None:
    """注册或清空全局媒体 publisher。``None`` = 卸载,回落到 data URL。"""
    media_host_registry.set(publisher)
    if publisher is None:
        logger.info("[MediaHost] 已清空媒体 publisher(回落 data URL)")
    else:
        name = getattr(publisher, "__qualname__", None) or getattr(publisher, "__name__", repr(publisher))
        logger.info(f"[MediaHost] 已注册媒体 publisher: {name}")


def get_media_publisher() -> Optional[MediaPublisher]:
    """当前已注册的 publisher;未注册返回 ``None``。"""
    return media_host_registry.get()


async def materialize(data: bytes, mime: str = "image/png") -> Optional[str]:
    """把媒体 bytes 变成公网 URL。

    - 未注册 publisher → 返回 ``None``(调用方自行 data URL / 透传)
    - 已注册 → 调用 publisher;外部异常统一包装为 ``MediaPublishError``,
      避免开源侧依赖闭源错误类型
    """
    publisher = media_host_registry.get()
    if publisher is None:
        return None
    try:
        return await publisher(data, mime)
    except MediaPublishError:
        raise
    except Exception as exc:  # noqa: BLE001 — 第三方/闭源错误类型不泄漏
        raise MediaPublishError(f"媒体发布失败({type(exc).__name__}: {exc})") from exc


__all__ = [
    "MediaPublisher",
    "MediaPublishError",
    "MediaHostRegistry",
    "media_host_registry",
    "set_media_publisher",
    "get_media_publisher",
    "materialize",
]
