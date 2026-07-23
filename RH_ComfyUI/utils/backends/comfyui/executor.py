"""ComfyUI Adapter — 实现 Adapter 接口

支持两种模式:
- declarative: 由 NodeDef.mappings 声明的映射规则直接生效
- programmatic: 调用 NodeDef.mapper_func 自行处理

新增:
- capabilities() 声明:支持所有图/视频/音频类任务,sync 模式,priority 中等
- progress_callback 透传:ComfyUI WebSocket 事件直接转发为 ProgressEvent
"""

from __future__ import annotations

import io
import asyncio
from typing import Any, Optional
from pathlib import Path

from gsuid_core.logger import logger

from .api import ComfyUIAPI
from ..base import Adapter
from ...core.types import NodeOutput, ProgressEvent, CapabilityManifest
from ...core.request import (
    TASK_MIME_MAP,
    TASK_OUTPUT_MAP,
    TASK_DISPLAY_NAME,
    OutputType,
    GenerationRequest,
)
from ...core.pipeline import NodeDef, MappingRule
from ...resource.RESOURCE_PATH import WORKFLOW_PATH, load_workflow


class ComfyUIAdapter(Adapter):
    """ComfyUI 后端"""

    name = "comfyui"

    def __init__(self) -> None:
        self.api = ComfyUIAPI()

    # ── Adapter 接口 ──

    async def check_available(self) -> bool:
        from ....rh_config.comfyui_config import SERVICE_CONFIG

        url: str = SERVICE_CONFIG.get_config("ComfyUI_BaseURL").data
        return bool(url) and url != "127.0.0.1:8188"

    async def get_unavailable_reason(self) -> str:
        return "未配置 ComfyUI 服务地址,请在 Web 控制台配置 ComfyUI_BaseURL"

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            supported_tasks=["image", "video", "music", "speech"],
            supported_params=[
                "prompt",
                "negative_prompt",
                "width",
                "height",
                "duration",
                "images",
                "reference_audio",
                "mood",
                "seed",
            ],
            output_mime=["image/png", "video/mp4", "audio/mpeg", "audio/wav"],
            mode="sync",
            priority=50,
        )

    async def execute(
        self,
        request: GenerationRequest,
        node: NodeDef,
        *,
        on_progress=None,
    ) -> NodeOutput:
        # 1. 加载工作流 JSON
        if node.workflow_file is None:
            raise RuntimeError(f"ComfyUI 节点 {node.name} 缺少 workflow_file")

        workflow_path = self._resolve_workflow_path(node)
        if workflow_path is None:
            raise RuntimeError(f"ComfyUI 节点 {node.name} 工作流文件不存在: {node.workflow_file}")
        workflow = load_workflow(workflow_path)

        # 2. 进度上报:开始
        await _emit(on_progress, ProgressEvent(stage="preparing", percent=5, message="准备 ComfyUI 工作流"))

        # 3. 参数映射
        if node.mode == "declarative":
            workflow = await self._apply_declarative_mappings(request, node.mappings, workflow)
        elif node.mode == "programmatic" and node.mapper_func:
            workflow = await node.mapper_func(request, workflow, self.api)
        else:
            raise RuntimeError(f"节点 {node.name} 映射模式无效: mode={node.mode}")

        # 3.5 mapper 可能在调用过程中请求使用另一个工作流文件
        # (例如统一 videogen 节点下,带图场景需要 i2v workflow 代替 t2v)
        override_name = self.api.consume_workflow_override()
        if override_name and override_name != node.workflow_file:
            override_path = self._resolve_workflow_file(node, override_name)
            if override_path is None:
                raise RuntimeError(f"ComfyUI 节点 {node.name} 申请的工作流覆盖文件不存在: {override_name}")
            logger.info(f"[ComfyUI] 节点 {node.name} 加载覆盖工作流: {override_name}")
            workflow = load_workflow(override_path)
            # 重要: 使用新工作流后,需重新应用映射(mapping 中指定的 node_id 以
            # 新加载的 workflow 为准)。在统一 videogen mapper 中
            # mapping 是 programmatic,已根据 image_count 选择了对应节点 ID,
            # 但需要重新调用一次 mapper 以填充新 workflow 的节点。
            if node.mode == "programmatic" and node.mapper_func:
                workflow = await node.mapper_func(request, workflow, self.api)
            elif node.mode == "declarative":
                workflow = await self._apply_declarative_mappings(request, node.mappings, workflow)

        # 4. 执行
        output_type = TASK_OUTPUT_MAP[request.task_type]
        mime_type = TASK_MIME_MAP[request.task_type]

        if on_progress:
            await on_progress(ProgressEvent(stage="running", percent=20, message="ComfyUI 生成中"))

        if output_type == OutputType.IMAGE:
            image = await self.api.generate_image_by_prompt(workflow)
            await _emit(on_progress, ProgressEvent(stage="done", percent=100, message="图片已生成"))

            def _to_png(img) -> bytes:
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return buf.getvalue()

            data = await asyncio.to_thread(_to_png, image)
            return NodeOutput(
                status="ok",
                output_type="image",
                data=data,
                mime_type=mime_type,
            )
        elif output_type == OutputType.VIDEO:
            video = await self.api.generate_video_by_prompt(workflow)
            await _emit(on_progress, ProgressEvent(stage="done", percent=100, message="视频已生成"))
            if video is None:
                raise RuntimeError(f"ComfyUI 视频生成失败: {node.name}")
            return NodeOutput(
                status="ok",
                output_type="video",
                data=video,
                mime_type=mime_type,
            )
        elif output_type == OutputType.AUDIO:
            audio = await self.api.generate_audio_by_prompt(workflow)
            await _emit(on_progress, ProgressEvent(stage="done", percent=100, message="音频已生成"))
            if audio is None:
                raise RuntimeError(f"ComfyUI 音频生成失败: {node.name}")
            return NodeOutput(
                status="ok",
                output_type="audio",
                data=audio,
                mime_type=mime_type,
            )

        raise RuntimeError(f"未知的输出类型: {output_type}")

    # ── 声明式映射(原 ComfyUIBackend 的实现,内部使用) ──

    async def _apply_declarative_mappings(
        self,
        request: GenerationRequest,
        mappings: list[MappingRule],
        workflow: dict[str, Any],
    ) -> dict[str, Any]:
        """将声明式映射规则应用到工作流"""
        for rule in self._normalize_mappings(mappings):
            optional = bool(rule["optional"]) if "optional" in rule else False
            target = self._get_required_str(rule, "target")
            value = await self._resolve_mapping_value(request, rule)

            if value is None:
                if optional:
                    continue
                raise RuntimeError(f"声明式映射缺少必要值: target={target}")

            template = rule["template"] if "template" in rule else None
            if template is not None:
                if not isinstance(template, str):
                    raise RuntimeError(f"声明式映射 template 必须是字符串: target={target}")
                value = template.replace("{value}", str(value))

            mapping_type = rule["type"] if "type" in rule else ""
            if mapping_type in {"image", "upload_image"}:
                if not isinstance(value, bytes):
                    if optional:
                        continue
                    raise RuntimeError(f"声明式映射上传图片需要 bytes 输入: target={target}")
                value = await self.api.upload_image(value)
            elif mapping_type in {"image_list", "upload_image_list"}:
                if not isinstance(value, list):
                    if optional:
                        continue
                    raise RuntimeError(f"声明式映射上传图片列表需要 list[bytes] 输入: target={target}")
                uploaded_images: list[str] = []
                for item in value:
                    if not isinstance(item, bytes):
                        raise RuntimeError(f"声明式映射图片列表包含非 bytes 元素: target={target}")
                    uploaded_images.append(await self.api.upload_image(item))
                value = uploaded_images
            elif mapping_type:
                raise RuntimeError(f"未知声明式映射类型: {mapping_type}")

            self._set_workflow_value(workflow, target, value)

        return workflow

    @staticmethod
    def _normalize_mappings(mappings: list[MappingRule]) -> list[dict[str, Any]]:
        # 复制成可变 dict,供下游 resolver 读取(不改动 NodeDef 上的规则)
        return [dict(m) for m in mappings]

    async def _resolve_mapping_value(self, request: GenerationRequest, rule: dict[str, Any]) -> Any:
        if "value" in rule:
            return rule["value"]
        source = self._get_required_str(rule, "source")
        value = self._read_request_path(request, source)
        if value is None and "default" in rule:
            return rule["default"]
        return value

    @staticmethod
    def _read_request_path(request: GenerationRequest, source: str) -> Any:
        parts = source.split(".")
        if not parts:
            return None
        root = parts[0]
        if root == "task_type":
            value: Any = request.task_type
        elif root == "prompt":
            value = request.prompt
        elif root == "negative_prompt":
            value = request.negative_prompt
        elif root == "images":
            value = request.images
        elif root == "reference_audio":
            value = request.reference_audio
        elif root == "width":
            value = request.width
        elif root == "height":
            value = request.height
        elif root == "duration":
            value = request.duration
        elif root == "seed":
            value = request.seed
        elif root == "ratio":
            value = request.ratio
        elif root == "resolution":
            value = request.resolution
        elif root == "generate_audio":
            value = bool(request.generate_audio)
        elif root == "watermark":
            value = request.watermark
        elif root == "camera_fixed":
            value = request.camera_fixed
        elif root == "return_last_frame":
            value = request.return_last_frame
        elif root == "service_tier":
            value = request.service_tier
        elif root == "mood":
            value = request.mood
        elif root == "voice_id":
            value = request.voice_id
        elif root == "speed":
            value = request.speed
        elif root == "language_boost":
            value = request.language_boost
        elif root == "model":
            value = request.model
        elif root == "params":
            value = request.params
        else:
            return None

        for part in parts[1:]:
            if isinstance(value, list):
                if not part.isdigit():
                    return None
                index = int(part)
                if index >= len(value):
                    return None
                value = value[index]
            elif isinstance(value, dict):
                if part not in value:
                    return None
                value = value[part]
            else:
                return None
        return value

    @staticmethod
    def _set_workflow_value(workflow: dict[str, Any], target: str, value: Any) -> None:
        parts = target.split(".")
        if len(parts) < 2:
            raise RuntimeError(f"声明式映射 target 至少需要两级路径: {target}")

        current: Any = workflow
        for part in parts[:-1]:
            if not isinstance(current, dict):
                raise RuntimeError(f"声明式映射 target 中间路径不是 dict: {target}")
            if part not in current:
                raise RuntimeError(f"声明式映射 target 路径不存在: {target}")
            current = current[part]

        if not isinstance(current, dict):
            raise RuntimeError(f"声明式映射 target 父路径不是 dict: {target}")
        current[parts[-1]] = value

    @staticmethod
    def _get_required_str(rule: dict[str, Any], key: str) -> str:
        if key not in rule:
            raise RuntimeError(f"声明式映射缺少字段: {key}")
        value = rule[key]
        if not isinstance(value, str):
            raise RuntimeError(f"声明式映射字段必须是字符串: {key}")
        return value

    def _resolve_workflow_path(self, node: NodeDef) -> Optional[Path]:
        """解析工作流 JSON 文件路径(默认走 node.workflow_file)"""
        if node.workflow_file is None:
            return None
        return self._resolve_workflow_file(node, node.workflow_file)

    @staticmethod
    def _resolve_workflow_file(node: NodeDef, filename: str) -> Optional[Path]:
        """按文件名解析工作流路径(不依赖 node.workflow_file)"""
        if not filename:
            return None
        task_cn = TASK_DISPLAY_NAME.get(node.task_type, node.task_type.value)
        workflow_in_data = WORKFLOW_PATH / task_cn / filename
        if workflow_in_data.exists():
            return workflow_in_data
        return None


async def _emit(cb, event: ProgressEvent) -> None:
    if cb is None:
        return
    try:
        await cb(event)
    except Exception:  # noqa: BLE001
        pass
