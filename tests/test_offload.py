"""RH 生成路径 CPU 卸载:base64 语义不变。"""

from __future__ import annotations

import inspect
import asyncio
import base64

from RH_ComfyUI.utils import offload
from RH_ComfyUI.utils.mappers import seedream as seedream_mod
from RH_ComfyUI.utils.mappers import minimax_image2image as minimax_i2i
from RH_ComfyUI.utils.backends.mimo import api as mimo_api
from RH_ComfyUI.utils.backends.gpt_image2 import api as gpt_api
from RH_ComfyUI.utils.backends.minimax import api as minimax_api


def test_b64ascii_matches_stdlib() -> None:
    raw = b"\x00\x01\xff" * 50
    assert offload.b64ascii(raw) == base64.b64encode(raw).decode("ascii")
    assert offload.b64ascii(b"") == ""


def test_data_url_async_matches_sync() -> None:
    large = b"p" * (offload.OFFLOAD_MIN_BYTES + 8)

    async def _run() -> None:
        assert await offload.data_url_async(large, "image/jpeg") == offload.data_url(large, "image/jpeg")
        assert await offload.b64ascii_async(large) == offload.b64ascii(large)

    asyncio.run(_run())


def test_seedream_and_minimax_encode_off_loop() -> None:
    src = inspect.getsource(seedream_mod.seedream_mapper)
    assert "asyncio.to_thread(_encode_images_to_data_urls" in src
    i2i = inspect.getsource(minimax_i2i.minimax_image01_img2img_mapper)
    assert "asyncio.to_thread(api._encode_image_to_base64" in i2i


def test_mimo_gpt_minimax_download_decode_off_loop() -> None:
    mimo_src = inspect.getsource(mimo_api.MIMOAPI.generate_speech)
    assert "data_url_async" in mimo_src
    gpt_dl = inspect.getsource(gpt_api.GPTImage2API._download_image_from_url)
    assert "asyncio.to_thread" in gpt_dl
    gpt_parse = inspect.getsource(gpt_api.GPTImage2API._parse_image_from_content)
    assert "asyncio.to_thread(self._decode_base64_image" in gpt_parse
    mm_dl = inspect.getsource(minimax_api.MiniMaxAPI._download_image_from_url)
    assert "asyncio.to_thread" in mm_dl
