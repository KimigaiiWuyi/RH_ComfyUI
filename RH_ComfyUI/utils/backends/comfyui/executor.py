"""ComfyUI 后端执行器 — 实现 Backend 接口"""

from __future__ import annotations

import io
from typing import Any, Optional
from pathlib import Path

from .api import ComfyUIAPI
from ..base import Backend
from ...core.request import (
    TASK_MIME_MAP,
    TASK_OUTPUT_MAP,
    TASK_DISPLAY_NAME,
    OutputType,
    GenerationResult,
    GenerationRequest,
)
from ...core.pipeline import PipelineDef
from ...resource.RESOURCE_PATH import WORKFLOW_PATH, load_workflow


class ComfyUIBackend(Backend):
    """ComfyUI 后端"""

    name = "comfyui"

    def __init__(self) -> None:
        self.api = ComfyUIAPI()

    async def check_available(self) -> bool:
        from ....rh_config.comfyui_config import RHCOMFYUI_CONFIG

        url: str = RHCOMFYUI_CONFIG.get_config("ComfyUI_BaseURL").data
        return bool(url) and url != "127.0.0.1:8188"

    async def get_unavailable_reason(self) -> str:
        return "未配置 ComfyUI 服务地址，请在 Web 控制台配置 ComfyUI_BaseURL"

    async def execute(self, request: GenerationRequest, pipeline: PipelineDef) -> GenerationResult:
        # 1. 加载工作流 JSON
        if pipeline.workflow_file is None:
            raise RuntimeError(f"ComfyUI Pipeline {pipeline.name} 缺少 workflow_file")

        workflow_path = self._resolve_workflow_path(pipeline)
        if workflow_path is None:
            raise RuntimeError(f"ComfyUI Pipeline {pipeline.name} 工作流文件不存在: {pipeline.workflow_file}")
        workflow = load_workflow(workflow_path)

        # 2. 参数映射
        if pipeline.mode == "declarative":
            workflow = await self._apply_declarative_mappings(request, pipeline.mappings, workflow)
        elif pipeline.mode == "programmatic" and pipeline.mapper_func:
            workflow = await pipeline.mapper_func(request, workflow, self.api)
        else:
            raise RuntimeError(f"Pipeline {pipeline.name} 映射模式无效: mode={pipeline.mode}")

        # 3. 执行
        output_type = TASK_OUTPUT_MAP[request.task_type]
        mime_type = TASK_MIME_MAP[request.task_type]

        if output_type == OutputType.IMAGE:
            image = await self.api.generate_image_by_prompt(workflow)
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            image_bytes = buf.getvalue()
            return GenerationResult(
                output_type=OutputType.IMAGE,
                data=image_bytes,
                mime_type=mime_type,
            )
        elif output_type == OutputType.VIDEO:
            video = await self.api.generate_video_by_prompt(workflow)
            if video is None:
                raise RuntimeError(f"ComfyUI 视频生成失败: {pipeline.name}")
            return GenerationResult(
                output_type=OutputType.VIDEO,
                data=video,
                mime_type=mime_type,
            )
        elif output_type == OutputType.AUDIO:
            audio = await self.api.generate_audio_by_prompt(workflow)
            if audio is None:
                raise RuntimeError(f"ComfyUI 音频生成失败: {pipeline.name}")
            return GenerationResult(
                output_type=OutputType.AUDIO,
                data=audio,
                mime_type=mime_type,
            )

        raise RuntimeError(f"未知的输出类型: {output_type}")

    async def _apply_declarative_mappings(
        self,
        request: GenerationRequest,
        mappings: dict | list[dict[str, Any]],
        workflow: dict[str, Any],
    ) -> dict[str, Any]:
        """将声明式映射规则应用到工作流

        支持两种 YAML 格式：
        1. 旧格式：mappings.prompt.node_id + input_key
        2. 新格式：mappings 列表，每项使用 source/value + target
        """
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
    def _normalize_mappings(mappings: dict | list[dict[str, Any]]) -> list[dict[str, Any]]:
        """标准化 mappings，兼容旧字典格式和新列表格式"""
        if isinstance(mappings, list):
            return mappings

        normalized: list[dict[str, Any]] = []
        for source, mapping in mappings.items():
            if not isinstance(mapping, dict):
                raise RuntimeError(f"旧版声明式映射必须是字典: {source}")
            if "target" in mapping:
                rule = dict(mapping)
                if "source" not in rule and "value" not in rule:
                    rule["source"] = source
                normalized.append(rule)
                continue

            if "node_id" not in mapping or "input_key" not in mapping:
                raise RuntimeError(f"旧版声明式映射缺少 node_id/input_key: {source}")
            rule = {
                "source": source,
                "target": f"{mapping['node_id']}.inputs.{mapping['input_key']}",
            }
            for key in ("default", "type", "template", "optional", "value"):
                if key in mapping:
                    rule[key] = mapping[key]
            normalized.append(rule)
        return normalized

    async def _resolve_mapping_value(self, request: GenerationRequest, rule: dict[str, Any]) -> Any:
        """解析映射值，支持 source、value、default"""
        if "value" in rule:
            return rule["value"]

        source = self._get_required_str(rule, "source")
        value = self._read_request_path(request, source)
        if value is None and "default" in rule:
            return rule["default"]
        return value

    @staticmethod
    def _read_request_path(request: GenerationRequest, source: str) -> Any:
        """读取 GenerationRequest 路径，如 prompt、images.0、extra.lora_name"""
        parts = source.split(".")
        if not parts:
            return None

        root = parts[0]
        if root == "task_type":
            value: Any = request.task_type
        elif root == "prompt":
            value = request.prompt
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
        elif root == "negative_prompt":
            value = request.negative_prompt
        elif root == "model":
            value = request.model
        elif root == "extra":
            value = request.extra
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
        """按 target 路径写入 workflow，如 108.inputs.text"""
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
        """读取必填字符串字段"""
        if key not in rule:
            raise RuntimeError(f"声明式映射缺少字段: {key}")
        value = rule[key]
        if not isinstance(value, str):
            raise RuntimeError(f"声明式映射字段必须是字符串: {key}")
        return value

    @staticmethod
    def _resolve_workflow_path(pipeline: PipelineDef) -> Optional[Path]:
        """解析工作流 JSON 文件路径

        查找顺序：
        1. YAML 同目录
        2. WORKFLOW_PATH 按任务类型子目录
        """
        if pipeline.workflow_file is None:
            return None

        # 1. YAML 同目录
        yaml_dir = pipeline.yaml_path.parent / pipeline.workflow_file
        if yaml_dir.exists():
            return yaml_dir

        # 2. WORKFLOW_PATH 按中文任务类型子目录
        task_cn = TASK_DISPLAY_NAME.get(pipeline.task_type, pipeline.task_type.value)
        workflow_in_data = WORKFLOW_PATH / task_cn / pipeline.workflow_file
        if workflow_in_data.exists():
            return workflow_in_data

        return None
