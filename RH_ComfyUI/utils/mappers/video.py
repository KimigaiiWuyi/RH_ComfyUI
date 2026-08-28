"""视频生成映射函数(统一 videogen 入口)

设计要点:
- 一个 mapper 兼容 0 / 1 / 2 张图三种输入形态(Wan 2.2 的实际能力):
    * 0 张图  → 纯文生视频
    * 1 张图  → 图生视频(首帧)
    * 2 张图  → 首尾帧生视频(images[0]=首帧, images[1]=尾帧)
- 超过 2 张图不会到这里:`Wan22VideoModel.validate()` 已硬拒绝并引导去 Seedance。
- 支持 prompt 位置插值:
    用户可在 prompt 中用 "图片1" / "图片2" 指代传入的图,
    mapper 会按位置把代号替换为对应节点的标签文字,例如:
        "图片1 走向 图片2"
    表示"首帧 走向 尾帧"。这与 Seedance 的多模态引用保持一致心智模型。
- 多模态(音/视频参考)输入由 Seedance 后端在
  `mappers/seedance.py` 中通过 `ordered_content` 处理;
  本 mapper 只关注 ComfyUI Wan 工作流这一支。

工作流选择机制:
  ComfyUI adapter 默认根据 `node.workflow_file` 加载一个工作流 JSON。
  本 mapper 根据请求的图片数量决定实际需要的工作流文件名
  (wan2.2_t2v.json 或 wan2.2_i2v.json),并通过
  ``ComfyUIAPI.set_workflow_override()`` 覆盖当前调用使用的工作流。
  YAML 里的 ``workflow:`` 字段仅作为「默认/不存在覆盖时的可读性提示」。
"""

from __future__ import annotations

import re
from typing import Any

from ..core.request import GenerationRequest
from ..backends.comfyui.api import ComfyUIAPI

# ── Prompt 位置插值 ──────────────────────────────────────────────
#
# 匹配结构化 "[@参考图片1]" / "[参考图片1]" 与裸 "图片1"/"视频1"/"音频1" 等中文代号,
# 允许 "图片 1"(中间带空格)的形式。
# 索引上限 2:Wan 2.2 只支持首尾帧,没有 "图片3+";
# Seedance 等多参考节点若需要支持 3+ 图,请在其自己的 mapper 里扩展。

# 组1=结构化 kind(图片|视频|音频), 组2=其序号;
# 组3=裸 kind, 组4=其序号。结构化必须优先匹配,避免「[@参考图片1]」被拆坏。
_PROMPT_REF_PATTERN = re.compile(
    r"\[\s*@?\s*参考(图片|视频|音频)\s*(\d{1,2})\s*\]" r"|(图片|视频|音频)\s*(\d{1,2})"
)
# Wan 2.2 实际能消费的图索引上限(首帧=1, 尾帧=2)
_MAX_WAN_IMAGE_INDEX = 2


def _build_image_position_labels(n: int) -> list[str]:
    """生成第 i 张图在 prompt 中的位置插值标签(Wan 2.2 版本)

    n=0 → []
    n=1 → ["首帧图"]
    n=2 → ["首帧图", "尾帧图"]
    """
    if n <= 0:
        return []
    if n == 1:
        return ["首帧图"]
    # n >= 2
    return ["首帧图", "尾帧图"]


def interpolate_prompt_refs(prompt: str, *, image_count: int) -> str:
    """把 prompt 中的 "[@参考图片N]" / "[参考图片N]" / "图片N" 替换为对应的位置标签

    规则(Wan 2.2 视角):
    - "[@参考图片1]" / "[参考图片1]" / "图片1" / "图片 1" → "首帧图"
    - "[@参考图片2]" / "[参考图片2]" / "图片2" / "图片 2" → "尾帧图"
    - "图片3+"            → 越界,保持原样(路由层不会让 Wan 2.2 收到 3+ 张图)
    - 其他类型("视频1"/"音频1")不在 ComfyUI Wan 工作流中支持,保持原样
    """
    if not prompt or image_count <= 0:
        return prompt

    labels = _build_image_position_labels(image_count)

    def _replace(match: re.Match[str]) -> str:
        kind = match.group(1) or match.group(3)
        raw_idx = match.group(2) or match.group(4)
        if not kind or not raw_idx:
            return match.group(0)
        idx = int(raw_idx)
        if kind != "图片" or idx < 1 or idx > len(labels):
            return match.group(0)
        return labels[idx - 1]

    return _PROMPT_REF_PATTERN.sub(_replace, prompt)


# ── 节点 ID 路由 ────────────────────────────────────────────────
#
# 兼容两套 Wan 2.2 workflow:
#   * 文生视频(wan2.2_t2v.json):节点 37=prompt, 33=duration,
#                                 44=width, 34=height
#   * 图生视频(wan2.2_i2v.json):节点 102=prompt, 294=duration,
#                                289=width, 290=height, 67=start_image
#                                67_end=end_image(可选,工作流扩展时启用)

_TEXT2VIDEO_NODE_IDS: dict[str, str] = {
    "prompt": "37",
    "width": "44",
    "height": "34",
    "duration": "33",
}

_IMG2VIDEO_NODE_IDS: dict[str, str] = {
    "prompt": "102",
    "width": "289",
    "height": "290",
    "duration": "294",
    "image": "67",  # start_image
}

# 与上述节点 ID 一一对应的工作流文件名
_T2V_WORKFLOW = "wan2.2_t2v.json"
_I2V_WORKFLOW = "wan2.2_i2v.json"

# 可选:首尾帧工作流中"尾帧"图像节点 ID(若工作流未配置则静默跳过)。
# 由用户在 workflow JSON 中维护;当前内置的 wan2.2_i2v.json 只支持 start_image,
# 因此 2 张图场景下尾帧会被忽略并打印 info(不报错 —— 行为可降级)。
_END_IMAGE_NODE_ID = "67_end"


def _pick_node_ids(image_count: int) -> dict[str, str]:
    """根据图片数量选择对应的 workflow 节点 ID 集合"""
    return _TEXT2VIDEO_NODE_IDS if image_count == 0 else _IMG2VIDEO_NODE_IDS


def _pick_workflow_name(image_count: int) -> str:
    """根据图片数量选择需要加载的工作流文件名"""
    return _T2V_WORKFLOW if image_count == 0 else _I2V_WORKFLOW


# ── 公共 mapper(供 YAML 引用) ──────────────────────────────────


async def wan_videogen_mapper(
    request: GenerationRequest,
    workflow: dict[str, Any],
    api: ComfyUIAPI,
) -> dict[str, Any]:
    """Wan 2.2 统一视频生成 mapper

    - 0 张图 → 走文生视频节点 ID(37/33/44/34)
    - 1 张图 → 走图生视频节点 ID(102/289/290/294/67),上传首帧
    - 2 张图 → 走图生视频节点 ID,上传首帧;若工作流提供了 67_end 节点
      (扩展版首尾帧工作流)则同时上传尾帧,否则降级只消费首帧并打 info。

    节点 ID 集合硬编码在工作流 JSON 中,见
    `resource/workflow/视频生成/wan2.2_*.json`。

    工作流覆盖:
    由于 ComfyUI adapter 已按 `node.workflow_file` 加载了一份工作流,
    但 t2v 和 i2v 是不同模型(节点 ID 不同),本 mapper 会在
    带图场景下通过 ``api.set_workflow_override()`` 要求 adapter
    重新加载 i2v.json 替换原 workflow。
    """
    image_count = len(request.images)
    node_ids = _pick_node_ids(image_count)
    target_workflow_name = _pick_workflow_name(image_count)

    # 若当前 workflow 不是目标工作流,请求 adapter 在后续步骤中
    # 用目标工作流替换。YAML 中声明的默认 workflow 仅作为初始 fallback。
    try:
        api.set_workflow_override(target_workflow_name)
    except AttributeError:
        # 旧版 ComfyUIAPI 没有该方法,保持原 workflow 不变(由调用方保证正确)
        pass

    # prompt 支持 "图片1/图片2" 位置插值
    prompt_text = interpolate_prompt_refs(request.prompt, image_count=image_count)

    # 写入各节点(节点 ID 取决于工作流;若 override 生效,以下写入
    # 在 adapter 重新加载后丢弃;否则继续生效)
    if "prompt" in node_ids:
        workflow[node_ids["prompt"]]["inputs"]["text"] = prompt_text
    if "width" in node_ids:
        workflow[node_ids["width"]]["inputs"]["value"] = request.width
    if "height" in node_ids:
        workflow[node_ids["height"]]["inputs"]["value"] = request.height
    if "duration" in node_ids:
        workflow[node_ids["duration"]]["inputs"]["value"] = request.duration

    # 图生视频:上传首帧(并按工作流能力尽量上传尾帧)
    if image_count >= 1 and "image" in node_ids:
        # 首帧 = images[0];Wan 2.2 i2v 工作流只暴露 start_image
        workflow[node_ids["image"]]["inputs"]["image"] = await api.upload_image(
            request.images[0],
        )

    # 尾帧(可选,仅当传入 2 张图且工作流扩展出 67_end 节点时启用)
    if image_count >= 2 and _END_IMAGE_NODE_ID in workflow:
        end_node = workflow[_END_IMAGE_NODE_ID]
        # 兼容性:节点可能是 LoadImage(image=...)或直接接收 image 字段;
        # 仅在节点确实持有 inputs.image 时写入,避免未知节点类型报错。
        if "inputs" in end_node and "image" in end_node.get("inputs", {}):
            end_node["inputs"]["image"] = await api.upload_image(request.images[1])
    elif image_count >= 2:
        # 当前内置的 wan2.2_i2v.json 只支持 start_image,
        # 尾帧会被忽略(不报错);提示用户升级工作流以真正消费尾帧。
        from gsuid_core.logger import logger

        logger.info(
            "[Wan 2.2] 收到首尾帧请求,但当前工作流仅支持 start_image;"
            "尾帧暂未使用。如需启用尾帧,请在工作流中新增 end_image 节点"
            "(默认节点 ID: %s) 并接到 WanVideoImageToVideoEncode 的 end_image。",
            _END_IMAGE_NODE_ID,
        )

    return workflow


async def wan_text2video_mapper(
    request: GenerationRequest,
    workflow: dict[str, Any],
    api: ComfyUIAPI,
) -> dict[str, Any]:
    """纯文生视频 mapper —— 无图片输入,走文生视频节点 ID"""
    return await wan_videogen_mapper(request, workflow, api)


async def wan_img2video_mapper(
    request: GenerationRequest,
    workflow: dict[str, Any],
    api: ComfyUIAPI,
) -> dict[str, Any]:
    """图生视频 mapper —— 至少 1 张图,走图生视频节点 ID

    注意:首尾帧(2 张图)也走该 mapper;尾帧由 wan_videogen_mapper 内部按工作流
    能力自动尝试注入 67_end 节点。
    """
    return await wan_videogen_mapper(request, workflow, api)


__all__ = [
    "wan_videogen_mapper",
    "wan_text2video_mapper",
    "wan_img2video_mapper",
    "interpolate_prompt_refs",
]
