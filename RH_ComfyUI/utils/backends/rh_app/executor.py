"""RunningHub 原生 AI 应用 Adapter"""

from __future__ import annotations

import json
from typing import Any

from .api import rh_app_api
from ..base import Adapter
from ...core.types import NodeOutput, ProgressEvent, CapabilityManifest
from ...core.request import (
    TASK_MIME_MAP,
    TASK_OUTPUT_MAP,
    OutputType,
    GenerationRequest,
)
from ...core.pipeline import NodeDef, MappingRule


def _guess_audio_upload_name(data: bytes) -> str:
    """按魔数给 RunningHub 上传一个带后缀的音频文件名。"""
    if data[:4] == b"RIFF":
        return "input.wav"
    if data[:4] == b"OggS":
        return "input.ogg"
    if data[:4] == b"ftyp" or data[4:8] == b"ftyp":
        return "input.m4a"
    return "input.mp3"


class RHAppAdapter(Adapter):
    """RunningHub 原生 AI 应用后端"""

    name = "rh_app"

    def __init__(self) -> None:
        self.api = rh_app_api

    # ── Adapter 接口 ──

    async def check_available(self) -> bool:
        from ....rh_config.comfyui_config import SERVICE_CONFIG

        key: str = SERVICE_CONFIG.get_config("RH_apikey").data
        return bool(key)

    async def get_unavailable_reason(self) -> str:
        return "未配置 RunningHub API Key,请在 Web 控制台配置 RH_apikey"

    def capabilities(self) -> CapabilityManifest:
        return CapabilityManifest(
            supported_tasks=["image", "speech"],
            supported_params=["prompt", "width", "height", "images", "reference_audio", "mood"],
            output_mime=["image/png", "image/jpeg", "audio/mpeg", "audio/wav"],
            mode="async_poll",
            priority=55,
        )

    async def execute(
        self,
        request: GenerationRequest,
        node: NodeDef,
        *,
        on_progress=None,
    ) -> NodeOutput:
        # 1. webapp_id
        from .config import is_rh_app_enabled, rh_app_disabled_reason

        webapp_id = node.workflow_file
        if not is_rh_app_enabled(node.name, webapp_id or ""):
            reason = rh_app_disabled_reason(node.name, node.display_name or node.name)
            raise RuntimeError(reason or f"{node.name} 未启用")
        if not webapp_id:
            raise RuntimeError(f"RH App 节点 '{node.name}' 缺少 workflow 字段,请填写 webappId 作为 workflow 值")

        # 2. 构建 nodeInfoList
        node_info_list = await self._build_node_info_list(request, node)
        if not node_info_list:
            raise RuntimeError(f"RH App 节点 '{node.name}' 构建的 nodeInfoList 为空")

        # 3. 提交任务
        await _emit(on_progress, ProgressEvent(stage="submitting", percent=10, message="提交任务"))
        # 统计:最终 nodeInfoList + 调用方 prompt(AI 应用侧一般不改写 prompt 文本)
        from ....core.telemetry.wire_capture import set_wire_audit

        set_wire_audit(
            prompt=request.prompt or "",
            request={
                "webappId": webapp_id,
                "nodeInfoList": node_info_list,
            },
        )
        submit_result = await self.api.submit_task(webapp_id, node_info_list)
        task_id = submit_result.get("taskId")
        if not task_id:
            error = submit_result.get("errorMessage", "未知错误")
            raise RuntimeError(f"[RHApp] 提交任务失败: {error}")

        # 落库 vendor_task_id 供 resume;无 AI 应用 cancel API → 不挂 cancel_remote
        from ....core.dispatch.active_tasks import get_active_task_registry

        await get_active_task_registry().bind_vendor_task(
            vendor_task_id=str(task_id),
            channel_name="rh_app",
            cancel_remote=None,
        )

        # 预校验
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

        # 4. 轮询(rh_app 禁止 cancel;仅 resume_poll 可续查)
        await _emit(on_progress, ProgressEvent(stage="queued", percent=20, message="任务排队中"))
        results = await self.api.wait_for_result(task_id)
        await _emit(on_progress, ProgressEvent(stage="done", percent=100, message="完成"))

        # 5. 封装结果
        return await self._process_results(results, request, node)

    # ── 内部方法(声明式映射) ──

    async def _build_node_info_list(
        self,
        request: GenerationRequest,
        node: NodeDef,
    ) -> list[dict[str, Any]]:
        node_info_list: list[dict[str, Any]] = []

        for rule in self._normalize_mappings(node.mappings):
            optional = bool(rule.get("optional", False))
            target = self._get_required_str(rule, "target")
            description = rule.get("description", "")

            parts = target.split(".")
            if len(parts) != 2:
                raise RuntimeError(f"[RHApp] 映射 target 格式错误,应为 'nodeId.fieldName': {target}")
            node_id, field_name = parts[0], parts[1]

            value = await self._resolve_mapping_value(request, rule)
            if value is None:
                if optional:
                    continue
                raise RuntimeError(f"[RHApp] 映射缺少必要值: target={target}")

            template = rule.get("template")
            if template is not None:
                if not isinstance(template, str):
                    raise RuntimeError(f"[RHApp] 映射 template 必须是字符串: target={target}")
                value = template.replace("{value}", str(value))

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
                uploaded: list[str] = []
                for i, item in enumerate(value):
                    if not isinstance(item, bytes):
                        raise RuntimeError(f"[RHApp] 映射图片列表包含非 bytes 元素: target={target}")
                    uploaded.append(await self.api.upload_file(item, filename=f"input_{i}.png"))
                value = ",".join(uploaded) if len(uploaded) > 1 else uploaded[0]
            elif mapping_type in {"audio", "upload_audio"}:
                if not isinstance(value, bytes):
                    if optional:
                        continue
                    raise RuntimeError(f"[RHApp] 映射上传音频需要 bytes 输入: target={target}")
                value = await self.api.upload_file(value, filename=_guess_audio_upload_name(value))
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
        elif root in ("params", "extra"):
            value = request.params if root == "params" else request.extra
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
        if key not in rule:
            raise RuntimeError(f"[RHApp] 映射缺少字段: {key}")
        value = rule[key]
        if not isinstance(value, str):
            raise RuntimeError(f"[RHApp] 映射字段必须是字符串: {key}")
        return value

    # ── 结果处理 ──

    async def _process_results(
        self,
        results: list[dict[str, Any]],
        request: GenerationRequest,
        node: NodeDef,
    ) -> NodeOutput:
        if not results:
            raise RuntimeError("[RHApp] 任务完成但未返回任何结果")

        result_item = results[0]
        file_url = result_item.get("url")
        output_type_str = result_item.get("outputType", "").lower()
        text_content = result_item.get("text")

        output_type = TASK_OUTPUT_MAP.get(request.task_type, OutputType.IMAGE)

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
        mime_type = ext_mime_map.get(
            output_type_str,
            TASK_MIME_MAP.get(request.task_type, "application/octet-stream"),
        )

        if text_content is not None and not file_url:
            data = str(text_content).encode("utf-8")
            return NodeOutput(
                status="ok",
                output_type="image",
                data=data,
                mime_type="text/plain",
            )

        if not file_url:
            raise RuntimeError(f"[RHApp] 结果中没有文件 URL: {result_item}")

        from ..http_retry import download_with_network_retry

        file_data = await download_with_network_retry(file_url, timeout=120, label="RHApp")

        return NodeOutput(
            status="ok",
            output_type=output_type.value,
            data=file_data,
            mime_type=mime_type,
            raw={"file_url": file_url},
        )


async def _emit(cb, event: ProgressEvent) -> None:
    if cb is None:
        return
    try:
        await cb(event)
    except Exception:  # noqa: BLE001
        pass


# 向后兼容
RHAppBackend = RHAppAdapter
