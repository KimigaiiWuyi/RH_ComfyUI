"""媒体外链化扩展点 — bytes → 上游可 GET 的公网 URL

设计动机
--------
部分上游对 base64 body 有大小限制(超限 413),生产环境通常把参考图
上传到对象存储再传公网 URL。对象存储出口属于**宿主/业务侧**能力,本引擎
只提供注册点,不绑定任何具体存储实现。

本模块提供与 ``channel_registry`` 同构的扩展点:

- 无 publisher 注册 → ``materialize()`` 返回 ``None``,调用方回落 data URL
- 有 publisher 注册 → 调用 publisher;失败抛 ``MediaPublishError``
  (调用方翻成 retryable 错误以便 failover)

宿主插件在 ``@on_core_start`` 中注册::

    from RH_ComfyUI.core import set_media_publisher


    async def my_publish(data: bytes, mime: str) -> str:
        ...  # 返回可 GET 的 https URL
        return url


    set_media_publisher(my_publish)

引擎对外部实现零感知:只认 ``MediaPublisher`` 可调用契约。
"""

from __future__ import annotations

import threading
from typing import Callable, Optional, Awaitable

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
