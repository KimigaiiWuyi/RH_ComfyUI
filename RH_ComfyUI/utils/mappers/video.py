"""视频生成映射函数(统一 videogen 入口)

设计要点:
- 一个 mapper 兼容 0 / 1 / N 张图三种输入形态:
    * 0 张图  → 纯文生视频
    * 1 张图  → 图生视频(首帧)
    * N 张图  → 首尾帧生视频(图片 1=首帧,图片 2=尾帧,其余为参考)
- 支持 prompt 位置插值:
    用户可在 prompt 中用 "图片1" / "图片2" / ... 指代传入的多张参考图,
    mapper 会按位置把代号替换为对应节点的标签文字,例如:
        "图片1 走向 图片2"
    表示"图 1 走向 图 2"。这与 Seedance 的多模态引用保持一致心智模型。
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
# 匹配 "图片1"、"图片2"、"视频1"、"音频1" 等中文代号,
# 允许 "图片 1"(中间带空格)的形式。
# 注意: 必须同时兼容"图片1"和"图片N",其中 N 最大为 9(与 YAML 中
# image 端口的 max_items=9 保持一致)。

_PROMPT_REF_PATTERN = re.compile(r"(图片|视频|音频)\s*(\d{1,2})")


def _build_image_position_labels(n: int) -> list[str]:
    """生成第 i 张图在 prompt 中的位置插值标签

    例如 n=2 时返回 ["首帧图", "尾帧图"];n=3 时返回
    ["首帧图", "尾帧图", "参考图1"];n=1 时返回 ["首帧图"]。
    """
    if n <= 0:
        return []
    if n == 1:
        return ["首帧图"]
    if n == 2:
        return ["首帧图", "尾帧图"]
    labels = ["首帧图", "尾帧图"]
    for i in range(3, n + 1):
        labels.append(f"参考图{i - 2}")
    return labels


def interpolate_prompt_refs(prompt: str, *, image_count: int) -> str:
    """把 prompt 中的 "图片1/图片2/..." 替换为对应的位置标签

    规则:
    - "图片1" / "图片 1" → "首帧图"
    - "图片2" / "图片 2" → "尾帧图"
    - "图片3+"            → "参考图N"
    - 只在 prompt 中存在相应代号时才替换;越界的代号(>image_count)保持原样
    - 其他类型("视频1"/"音频1")不在 ComfyUI Wan 工作流中支持,保持原样
    """
    if not prompt or image_count <= 0:
        return prompt

    labels = _build_image_position_labels(image_count)

    def _replace(match: re.Match[str]) -> str:
        kind = match.group(1)
        idx = int(match.group(2))
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
#                                289=width, 290=height, 67=image

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
    "image": "67",
}

# 与上述节点 ID 一一对应的工作流文件名
_T2V_WORKFLOW = "wan2.2_t2v.json"
_I2V_WORKFLOW = "wan2.2_i2v.json"


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
    - 1+ 张图 → 走图生视频节点 ID(102/289/290/294/67),自动上传首帧

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

    # 图生视频:上传首帧
    if image_count >= 1 and "image" in node_ids:
        # 首帧 = images[0];若用户传 2+ 张,尾帧暂不上传(交给更专业
        # 的 Seedance 多模态节点处理;此处保留首帧即可)
        workflow[node_ids["image"]]["inputs"]["image"] = await api.upload_image(
            request.images[0],
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
    """图生视频 mapper —— 至少 1 张图,走图生视频节点 ID"""
    return await wan_videogen_mapper(request, workflow, api)


__all__ = [
    "wan_videogen_mapper",
    "wan_text2video_mapper",
    "wan_img2video_mapper",
    "interpolate_prompt_refs",
]
