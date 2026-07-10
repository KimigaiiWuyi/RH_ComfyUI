"""视频预处理下沉回归 — normalize() 统一处理 images 与 ordered_content 图片项

回归点:入口层(api.submit)不再自带视频参考图预处理;三入口都靠
VideoGenerationBase.normalize()(run() 内)完成等比缩放,少一处会导致
超大参考图直传上游。
"""

import io
from typing import Any, Optional

from PIL import Image

from RH_ComfyUI.core.base.video import VideoGenerationBase
from RH_ComfyUI.core.schema.card import ModelCard
from RH_ComfyUI.core.schema.types import (
    MediaRef,
    PortSpec,
    PortType,
    MediaKind,
    NodeOutput,
    ContentItem,
    ContentItemType,
)
from RH_ComfyUI.core.schema.request import TaskType, GenerationRequest
from RH_ComfyUI.core.channels.channel import LocalChannel, ChannelBinding


def _big_png(width: int = 2000, height: int = 1000) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def _size_of(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as img:
        return img.size


class _MiniVideoModel(VideoGenerationBase):
    name = "mini_video"
    display_name = "mini_video"
    card = ModelCard(description="x")

    def input_schema(self) -> dict[str, PortSpec]:
        return {"prompt": PortSpec(type=PortType.TEXT, required=True)}

    def channel_bindings(self) -> list[ChannelBinding]:
        return [ChannelBinding(LocalChannel("local"))]

    async def execute_on_channel(
        self, request: GenerationRequest, binding: ChannelBinding, *, on_progress: Optional[Any] = None
    ) -> NodeOutput:
        return NodeOutput()


def test_normalize_shrinks_images_and_ordered_content():
    model = _MiniVideoModel()
    req = GenerationRequest(
        task_type=TaskType.VIDEO,
        prompt="p",
        resolution="1080P",
        images=[_big_png()],
        ordered_content=[
            ContentItem(
                type=ContentItemType.IMAGE,
                media=MediaRef(kind=MediaKind.IMAGE, data=_big_png(), role="first_frame"),
            ),
            ContentItem(type=ContentItemType.TEXT, text="镜头拉远"),
        ],
    )
    out = model.normalize(req)

    assert out.resolution == "1080p"  # 大小写归一
    w, h = _size_of(out.images[0])
    assert max(w, h) <= 800  # 参考图等比缩放

    img_item = out.ordered_content[0]
    assert img_item.media is not None and img_item.media.data is not None
    w2, h2 = _size_of(img_item.media.data)
    assert max(w2, h2) <= 800  # ordered_content 图片项同样被预处理
    assert img_item.media.role == "first_frame"  # role 等元数据不丢

    assert out.ordered_content[1].text == "镜头拉远"  # 非图片项原样保留
