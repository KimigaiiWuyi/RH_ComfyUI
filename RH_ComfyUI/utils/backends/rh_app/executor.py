"""RunningHub 原生 AI 应用后端执行器 — 实现 Backend 接口"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx

from .api import rh_app_api
from ..base import Backend
from ...core.request import (
    TASK_MIME_MAP,
    TASK_OUTPUT_MAP,
    OutputType,
    GenerationResult,
    GenerationRequest,
)
from ...core.pipeline import PipelineDef


class RHAppBackend(Backend):
    """RunningHub 原生 AI 应用后端

    通过 RunningHub OpenAPI v2 直接调用 AI 应用（WebApp），
    无需 ComfyUI 工作流 JSON，使用 nodeInfoList 传递参数。
    """

    name = "rh_app"

    def __init__(self) -> None:
        self.api = rh_app_api

    async def check_available(self) -> bool:
        from ....rh_config.comfyui_config import RHCOMFYUI_CONFIG

        key: str = RHCOMFYUI_CONFIG.get_config("RH_apikey").data
        return bool(key)

    async def get_unavailable_reason(self) -> str:
        return "未配置 RunningHub API Key，请在 Web 控制台配置 RH_apikey"

    async def execute(self, request: GenerationRequest, pipeline: PipelineDef) -> GenerationResult:
        """执行 RH 原生 AI 应用任务

        流程：
        1. 从 pipeline.workflow_file 读取 webapp_id
        2. 根据声明式映射构建 nodeInfoList
        3. 上传图片（如有 IMAGE 类型映射）
        4. 提交任务
        5. 轮询等待结果
        6. 下载结果文件并封装为 GenerationResult
        """
        # 1. 获取 webapp_id
        webapp_id = pipeline.workflow_file
        if not webapp_id:
            raise RuntimeError(
                f"RH App Pipeline '{pipeline.name}' 缺少 workflow 字段，请填写 webappId 作为 workflow 值"
            )

        # 2. 构建 nodeInfoList
        node_info_list = await self._build_node_info_list(request, pipeline)
        if not node_info_list:
            raise RuntimeError(f"RH App Pipeline '{pipeline.name}' 构建的 nodeInfoList 为空")

        # 3. 提交任务
        submit_result = await self.api.submit_task(webapp_id, node_info_list)

        # 4. 检查提交结果
        task_id = submit_result.get("taskId")
        if not task_id:
            error = submit_result.get("errorMessage", "未知错误")
            raise RuntimeError(f"[RHApp] 提交任务失败: {error}")

        # 检查 promptTips 中的 node_errors（工作流预校验）
        prompt_tips_str = submit_result.get("promptTips", "")
        if prompt_tips_str:
            try:
                prompt_tips = json.loads(prompt_tips_str)
                node_errors = prompt_tips.get("node_errors", {})
                if node_errors:
                    errors = [f"节点 {nid}: {err}" for nid, err in node_errors.items()]
                    raise RuntimeError(f"[RHApp] 工作流节点错误: {'; '.join(errors)}")
            except json.JSONDecodeError:
                pass

        # 5. 轮询等待结果
        results = await self.api.wait_for_result(task_id)

        # 6. 处理结果
        return await self._process_results(results, request, pipeline)

    # ── 声明式映射 ──

    async def _build_node_info_list(
        self,
        request: GenerationRequest,
        pipeline: PipelineDef,
    ) -> List[Dict[str, Any]]:
        """根据声明式映射规则构建 nodeInfoList"""
        node_info_list: List[Dict[str, Any]] = []

        for rule in self._normalize_mappings(pipeline.mappings):
            optional = bool(rule.get("optional", False))
            target = self._get_required_str(rule, "target")
            description = rule.get("description", "")

            # 解析 target: "nodeId.fieldName"
            parts = target.split(".")
            if len(parts) != 2:
                raise RuntimeError(f"[RHApp] 映射 target 格式错误，应为 'nodeId.fieldName': {target}")
            node_id, field_name = parts[0], parts[1]

            # 解析值
            value = await self._resolve_mapping_value(request, rule)
            if value is None:
                if optional:
                    continue
                raise RuntimeError(f"[RHApp] 映射缺少必要值: target={target}")

            # 处理 template
            template = rule.get("template")
            if template is not None:
                if not isinstance(template, str):
                    raise RuntimeError(f"[RHApp] 映射 template 必须是字符串: target={target}")
                value = template.replace("{value}", str(value))

            # 处理图片上传
            mapping_type = rule.get("type", "")
            if mapping_type in {"image", "upload_image"}:
                if not isinstance(value, bytes):
                    if optional:
                        continue
                    raise RuntimeError(f"[RHApp] 映射上传图片需要 bytes 输入: target={target}")
                value = await self.api.upload_file(value, filename="input.png")
            elif mapping_type in {"image_list", "upload_image_list"}:
                if not isinstance(value, list):
                    if optional:
                        continue
                    raise RuntimeError(f"[RHApp] 映射上传图片列表需要 list[bytes] 输入: target={target}")
                uploaded: List[str] = []
                for i, item in enumerate(value):
                    if not isinstance(item, bytes):
                        raise RuntimeError(f"[RHApp] 映射图片列表包含非 bytes 元素: target={target}")
                    uploaded.append(await self.api.upload_file(item, filename=f"input_{i}.png"))
                # LIST 类型字段可能需要逗号分隔或列表格式，这里统一用逗号分隔字符串
                value = ",".join(uploaded) if len(uploaded) > 1 else uploaded[0]
            elif mapping_type:
                raise RuntimeError(f"[RHApp] 未知映射类型: {mapping_type}")

            node_info_list.append(
                {
                    "nodeId": node_id,
                    "fieldName": field_name,
                    "fieldValue": str(value),
                    "description": description,
                }
            )

        return node_info_list

    @staticmethod
    def _normalize_mappings(mappings: dict | list[dict[str, Any]]) -> list[dict[str, Any]]:
        """标准化 mappings，兼容旧字典格式和新列表格式"""
        if isinstance(mappings, list):
            return mappings

        normalized: list[dict[str, Any]] = []
        for source, mapping in mappings.items():
            if not isinstance(mapping, dict):
                raise RuntimeError(f"[RHApp] 旧版声明式映射必须是字典: {source}")

            # 新格式（已有 target）
            if "target" in mapping:
                rule = dict(mapping)
                if "source" not in rule and "value" not in rule:
                    rule["source"] = source
                normalized.append(rule)
                continue

            # 旧格式（node_id + field_name）
            if "node_id" not in mapping or "field_name" not in mapping:
                raise RuntimeError(f"[RHApp] 旧版声明式映射缺少 node_id/field_name: {source}")
            rule = {
                "source": source,
                "target": f"{mapping['node_id']}.{mapping['field_name']}",
            }
            for key in ("default", "type", "template", "optional", "value", "description"):
                if key in mapping:
                    rule[key] = mapping[key]
            normalized.append(rule)
        return normalized

    async def _resolve_mapping_value(self, request: GenerationRequest, rule: dict[str, Any]) -> Any:
        """解析映射值，支持 value、source、default"""
        if "value" in rule:
            return rule["value"]

        source = self._get_required_str(rule, "source")
        value = self._read_request_path(request, source)
        if value is None and "default" in rule:
            return rule["default"]
        return value

    @staticmethod
    def _read_request_path(request: GenerationRequest, source: str) -> Any:
        """读取 GenerationRequest 路径，如 prompt、images.0、extra.width"""
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
    def _get_required_str(rule: dict[str, Any], key: str) -> str:
        """读取必填字符串字段"""
        if key not in rule:
            raise RuntimeError(f"[RHApp] 映射缺少字段: {key}")
        value = rule[key]
        if not isinstance(value, str):
            raise RuntimeError(f"[RHApp] 映射字段必须是字符串: {key}")
        return value

    # ── 结果处理 ──

    async def _process_results(
        self,
        results: List[Dict[str, Any]],
        request: GenerationRequest,
        pipeline: PipelineDef,
    ) -> GenerationResult:
        """处理任务结果，下载文件并封装为 GenerationResult"""
        if not results:
            raise RuntimeError("[RHApp] 任务完成但未返回任何结果")

        result_item = results[0]
        file_url = result_item.get("url")
        output_type_str = result_item.get("outputType", "").lower()
        text_content = result_item.get("text")

        # 确定输出类型和 MIME 类型
        output_type = TASK_OUTPUT_MAP.get(request.task_type, OutputType.IMAGE)

        # 根据实际返回的文件扩展名推断 MIME 类型
        ext_mime_map = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp",
            "mp4": "video/mp4",
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "txt": "text/plain",
        }
        mime_type = ext_mime_map.get(output_type_str, TASK_MIME_MAP.get(request.task_type, "application/octet-stream"))

        # 纯文本输出（无 URL）
        if text_content is not None and not file_url:
            return GenerationResult(
                output_type=output_type,
                data=str(text_content).encode("utf-8"),
                mime_type="text/plain",
            )

        if not file_url:
            raise RuntimeError(f"[RHApp] 结果中没有文件 URL: {result_item}")

        # 下载文件
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            response = await client.get(file_url)
            response.raise_for_status()
            file_data = response.content

        return GenerationResult(
            output_type=output_type,
            data=file_data,
            mime_type=mime_type,
        )
