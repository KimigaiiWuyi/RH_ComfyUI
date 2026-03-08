"""
模型可用性检查模块
提供运行时模型可用性检查和 RAG 预过滤
"""

import time
import asyncio
from enum import Enum, auto
from typing import Dict, List, Tuple, Callable, Optional
from dataclasses import dataclass

from ..rh_config.comfyui_config import RHCOMFYUI_CONFIG


class ModelRequirement(Enum):
    """模型依赖类型"""

    BLT_API = auto()  # 需要 BLT API Key
    COMFYUI_URL = auto()  # 需要 ComfyUI 服务地址
    RH_API = auto()  # 需要 RunningHub API Key


class ModelStatus(Enum):
    """模型可用状态"""

    AVAILABLE = "available"
    MISSING_BLT_API = "missing_blt_api"
    MISSING_COMFYUI = "missing_comfyui"
    MISSING_RH_API = "missing_rh_api"
    UNKNOWN = "unknown"


@dataclass
class ModelInfo:
    """模型信息"""

    name: str
    func: Callable
    requirements: List[ModelRequirement]
    category: str
    description: str


@dataclass
class AvailabilityResult:
    """可用性检查结果"""

    model_name: str
    status: ModelStatus
    is_available: bool
    reason: str
    last_checked: float

    def to_error_message(self) -> str:
        """转换为错误消息"""
        if self.is_available:
            return f"✅ 模型 {self.model_name} 可用"

        status_messages = {
            ModelStatus.MISSING_BLT_API: f"❌ 模型 {self.model_name} 不可用：未配置 BLT API Key",
            ModelStatus.MISSING_COMFYUI: f"❌ 模型 {self.model_name} 不可用：未配置 ComfyUI 服务地址",
            ModelStatus.MISSING_RH_API: f"❌ 模型 {self.model_name} 不可用：未配置 RunningHub API Key",
        }
        return status_messages.get(self.status, f"❌ 模型 {self.model_name} 不可用：{self.reason}")


class ModelAvailabilityChecker:
    """模型可用性检查器"""

    def __init__(self, cache_ttl: int = 60):
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, AvailabilityResult] = {}
        self._lock = asyncio.Lock()

    def _get_config(self, key: str) -> Optional[str]:
        """获取配置值"""
        try:
            config = RHCOMFYUI_CONFIG.get_config(key)
            value = config.data if config else None
            if not value or str(value).strip() == "":
                return None
            return str(value)
        except Exception:
            return None

    def _check_requirement(
        self, req: ModelRequirement
    ) -> Tuple[
        bool,
        Optional[ModelStatus],
        Optional[str],
    ]:
        """检查单个依赖"""
        if req == ModelRequirement.BLT_API:
            api_key = self._get_config("BLT_apikey")
            if not api_key:
                return False, ModelStatus.MISSING_BLT_API, "未配置 BLT API Key"
            return True, None, None

        elif req == ModelRequirement.COMFYUI_URL:
            url = self._get_config("ComfyUI_BaseURL")
            if not url or url == "127.0.0.1:8188":
                return False, ModelStatus.MISSING_COMFYUI, "未配置 ComfyUI 服务地址"
            return True, None, None

        elif req == ModelRequirement.RH_API:
            api_key = self._get_config("RH_apikey")
            if not api_key:
                return False, ModelStatus.MISSING_RH_API, "未配置 RunningHub API Key"
            return True, None, None

        return True, None, None

    async def check_model(
        self,
        model_info: ModelInfo,
        force: bool = False,
    ) -> AvailabilityResult:
        """检查单个模型可用性"""
        model_name = model_info.name
        now = time.time()

        # 检查缓存
        if not force and model_name in self._cache:
            cached = self._cache[model_name]
            if now - cached.last_checked < self.cache_ttl:
                return cached

        # 执行检查
        async with self._lock:
            # 双重检查
            if not force and model_name in self._cache:
                cached = self._cache[model_name]
                if now - cached.last_checked < self.cache_ttl:
                    return cached

            # 检查所有依赖
            all_available = True
            failed_status = ModelStatus.UNKNOWN
            failed_reason = "未知错误"

            for req in model_info.requirements:
                available, status, reason = self._check_requirement(req)
                if not available:
                    all_available = False
                    failed_status = status
                    failed_reason = reason
                    break

            result = AvailabilityResult(
                model_name=model_name,
                status=ModelStatus.AVAILABLE
                if all_available
                else (failed_status if failed_status else ModelStatus.UNKNOWN),
                is_available=all_available,
                reason=(failed_reason if failed_reason else "") if not all_available else "",
                last_checked=time.time(),
            )

            self._cache[model_name] = result
            return result

    async def filter_available(self, model_names: List[str], registry: Dict[str, ModelInfo]) -> List[str]:
        """过滤出可用的模型列表"""
        results = await self._check_multiple(model_names, registry)
        return [name for name, result in results.items() if result.is_available]

    async def _check_multiple(
        self, model_names: List[str], registry: Dict[str, ModelInfo]
    ) -> Dict[str, AvailabilityResult]:
        """批量检查多个模型"""
        results = {}
        tasks = []
        valid_names = []

        for name in model_names:
            model_info = registry.get(name)
            if model_info:
                tasks.append(self.check_model(model_info))
                valid_names.append(name)
            else:
                results[name] = AvailabilityResult(
                    model_name=name,
                    status=ModelStatus.UNKNOWN,
                    is_available=False,
                    reason=f"模型 {name} 未注册",
                    last_checked=time.time(),
                )

        if tasks:
            checked_results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, name in enumerate(valid_names):
                result = checked_results[i]
                if isinstance(result, Exception):
                    results[name] = AvailabilityResult(
                        model_name=name,
                        status=ModelStatus.UNKNOWN,
                        is_available=False,
                        reason=f"检查失败: {str(result)}",
                        last_checked=time.time(),
                    )
                else:
                    results[name] = result

        return results

    def clear_cache(self):
        """清除缓存"""
        self._cache.clear()


# 全局检查器实例
availability_checker = ModelAvailabilityChecker(cache_ttl=60)


class ModelUnavailableError(Exception):
    """模型不可用异常"""

    def __init__(self, message: str, model_name: str = "", status: ModelStatus = ModelStatus.UNKNOWN):
        super().__init__(message)
        self.model_name = model_name
        self.status = status
        self.message = message
