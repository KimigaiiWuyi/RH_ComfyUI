"""AIGCGenerationBase — 所有 AIGC 生成模型的顶层抽象基类

生命周期(模板方法 run() 固定,子类覆盖钩子):

    run(request)
      ├─ 1. validate(request)            # schema 通用校验 + 子类跨字段校验
      ├─ 2. normalize(request)           # 默认值填充 / 单位归一化(可覆盖)
      ├─ 3. balancer.order_candidates()  # 负载均衡选通道(多通道时)
      ├─ 4. execute_on_channel(...)      # ★ 子类核心:组装请求并执行
      │     ├─ 瞬时失败(transient,如 429/503)→ 原通道退避重试一次
      │     └─ 失败且可重试 → 记熔断 → 换下一个通道
      └─ 5. postprocess(output)          # 输出归一化(可覆盖)

设计约束:
- 本类不做计费、不做统计、不做全局限流 —— 那是 dispatcher 的职责。
- 本类不持有任何 HTTP 细节 —— 通信永远委托 ProviderChannel / backends 客户端。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Optional

from gsuid_core.logger import logger

from .errors import ChannelError, AllChannelsFailedError
from ..schema.card import ModelCard
from ..schema.types import PortSpec, PortType, NodeOutput, ProgressCallback
from ..schema.request import TaskType, GenerationRequest
from ..channels.channel import ChannelBinding

if TYPE_CHECKING:
    # 运行时延迟导入,避免 base ↔ routing 的循环(registry 依赖本模块)
    from ..routing.balancer import LoadBalancer
    from ...utils.core.pipeline import NodeDef


class AIGCGenerationBase(ABC):
    """所有 AIGC 生成模型的顶层 ABC

    子类(通常经由模态基类)必须提供:
        name / display_name / modality / card / input_schema() /
        channel_bindings() / execute_on_channel()
    """

    # ── 身份(子类必须覆盖;bridge 类可用实例属性遮蔽) ──
    name: str
    display_name: str
    modality: TaskType

    # 声明式/桥接模型携带的 NodeDef 投影;纯编程式模型为 None。
    # PipelineRegistry 目录即由此派生(见 utils/core/pipeline.py)。
    node: Optional["NodeDef"] = None

    # ── 元数据与计费 ──
    card: ModelCard
    point_cost: int = 2

    # ── 弹性:瞬时错误(429/503)在原通道退避重试一次的间隔秒数 ──
    transient_retry_delay: ClassVar[float] = 2.0

    # ── 路由 ──
    priority: int = 50  # 数字越大越优先
    execution_mode: ClassVar[str] = "sync"  # sync / async_poll

    # ── 并发:模型级并发上限(0=不限,只受全局闸约束) ──
    max_concurrency: ClassVar[int] = 0

    # ── 配置解析:该模型可用所需的 SERVICE_CONFIG 键 ──
    required_config: ClassVar[list[str]] = []

    # ═════════════════ 子类必须实现 ═════════════════

    @abstractmethod
    def input_schema(self) -> dict[str, PortSpec]:
        """编程式声明本模型的输入端口(驱动校验/调用方表单/Agent 文档)"""

    @abstractmethod
    def channel_bindings(self) -> list[ChannelBinding]:
        """声明本模型可执行的通道列表(1 个即单通道,多个则参与负载均衡)"""

    @abstractmethod
    async def execute_on_channel(
        self,
        request: GenerationRequest,
        binding: ChannelBinding,
        *,
        on_progress: Optional[ProgressCallback] = None,
    ) -> NodeOutput:
        """在指定通道上执行一次生成(子类的核心差异点)"""

    # ═════════════════ 可覆盖的钩子 ═════════════════

    def output_schema(self) -> dict[str, PortSpec]:
        """默认按模态推断,多输出模型(如返回尾帧)覆盖本方法"""
        defaults = {
            TaskType.IMAGE: ("image", PortType.OUTPUT_IMAGE),
            TaskType.VIDEO: ("video", PortType.OUTPUT_VIDEO),
            TaskType.MUSIC: ("audio", PortType.OUTPUT_AUDIO),
            TaskType.SPEECH: ("audio", PortType.OUTPUT_AUDIO),
            TaskType.ASR: ("text", PortType.OUTPUT_TEXT),
        }
        key, port_type = defaults[self.modality]
        return {key: PortSpec(type=port_type, description=f"生成的{key}")}

    def validate(self, request: GenerationRequest) -> None:
        """通用校验:required / 枚举值 / 数值范围 / 列表基数,基于 input_schema()

        子类覆盖时必须先 super().validate(request),再补跨字段约束。
        校验失败抛 ValidationError(message 面向最终用户,可直接透传)。
        """
        from .schema_validator import validate_against_schema

        validate_against_schema(request, self.input_schema(), model_name=self.name)

    def normalize(self, request: GenerationRequest) -> GenerationRequest:
        """默认值填充与归一化(如把 "1080P" 统一成 "1080p"),默认原样返回"""
        return request

    def estimate_cost(self, request: GenerationRequest) -> int:
        """本次请求的预估扣费(动态计费钩子)

        dispatcher 在校验通过后调用本方法确定 reserve 金额;默认恒等于
        point_cost(静态计费)。按参数分档计费的模型(如视频按分辨率×时长、
        flex/draft 档折扣)覆盖本方法,返回值即预扣与落库的积分数。
        必须是纯函数:只读 request,不做 IO,不抛业务异常(非法参数由
        validate() 先行拦截)。
        """
        return self.point_cost

    def supports(self, request: GenerationRequest) -> bool:
        """路由用输入档案匹配:该请求的输入形状是否落在本模型能力内"""
        from .schema_validator import schema_supports_request

        return schema_supports_request(request, self.input_schema())

    async def check_available(self) -> bool:
        """可用性 = 所有 required_config 均已配置(廉价,禁止网络探测)"""
        if self.required_config:
            from ...rh_config.comfyui_config import SERVICE_CONFIG

            for key in self.required_config:
                if not SERVICE_CONFIG.get_config(key).data:
                    return False
        # 多通道模型:至少一个通道可用即可用
        bindings = self.channel_bindings()
        if not bindings:
            return False
        for b in bindings:
            if await b.channel.check_available():
                return True
        return False

    async def unavailable_reason(self) -> str:
        if self.required_config:
            from ...rh_config.comfyui_config import SERVICE_CONFIG

            missing = [k for k in self.required_config if not SERVICE_CONFIG.get_config(k).data]
            if missing:
                return f"{self.display_name} 未配置: {', '.join(missing)}"
        return f"{self.display_name} 无可用通道"

    def postprocess(self, output: NodeOutput) -> NodeOutput:
        return output

    def balancer(self) -> "LoadBalancer":
        """负载均衡器实例;默认取全局单例,scope 为本模型名"""
        from ..routing.balancer import get_default_balancer

        return get_default_balancer()

    # ═════════════════ 模板方法(不建议覆盖) ═════════════════

    async def run(
        self,
        request: GenerationRequest,
        *,
        on_progress: Optional[ProgressCallback] = None,
    ) -> NodeOutput:
        """统一生命周期:校验 → 归一化 → 选通道 → 执行(带故障切换) → 后处理"""
        self.validate(request)
        request = self.normalize(request)

        bindings = self.channel_bindings()
        if not bindings:
            raise ChannelError(f"{self.display_name} 未声明任何执行通道")

        ordered = self.balancer().order_candidates(scope=self.name, candidates=bindings)

        from ..dispatch.concurrency import channel_slot, channel_slot_for_model, channel_has_capacity

        # 供应商软排序:满载(在途数 ≥ 并发上限)的通道排到末尾,优先溢到空闲供应商,
        # 避免阻塞在繁忙供应商的信号量上;组内保持负载均衡给出的顺序
        if len(ordered) > 1:
            free = [b for b in ordered if channel_has_capacity(b.channel.name)]
            if free:
                ordered = free + [b for b in ordered if b not in free]

        last_error: Optional[Exception] = None
        for binding in ordered:
            if not await binding.channel.check_available():
                continue
            output = None
            retried_transient = False
            while output is None:
                try:
                    # 两层闸嵌套:
                    #   1. 供应商全局闸(channel.name):防同一 channel 被多模型一起打爆
                    #   2. (model, channel) 闸:防单一模型在单一 channel 上挤爆
                    #      上限 = min(Channel_Concurrency, model.max_concurrency)
                    async with channel_slot(binding.channel.name):
                        async with channel_slot_for_model(self, binding.channel.name):
                            output = await self.execute_on_channel(request, binding, on_progress=on_progress)
                except ChannelError as e:
                    last_error = e
                    if not e.retryable:
                        # 参数类失败(换通道也没用)不计入熔断:通道本身是健康的,
                        # 用户反复提交坏请求不应把通道推入冷却期
                        raise
                    if e.transient and not retried_transient:
                        # 瞬时限流/过载(429/503):切通道会整单重跑更烧钱,
                        # 先在原通道退避重试一次;不计失败(通道是健康的)
                        retried_transient = True
                        logger.warning(
                            f"[{self.name}] 通道 {binding.channel.name} 瞬时失败({e}),"
                            f"{self.transient_retry_delay:.1f}s 后原通道重试"
                        )
                        await asyncio.sleep(self.transient_retry_delay)
                        continue
                    self.balancer().record_failure(scope=self.name, member=binding.channel.name)
                    if len(ordered) == 1:
                        raise
                    logger.warning(f"[{self.name}] 通道 {binding.channel.name} 失败({e}),切换下一通道")
                    break
            if output is None:
                continue
            self.balancer().record_success(scope=self.name, member=binding.channel.name)
            output.metadata.setdefault("channel", binding.channel.name)
            output.metadata.setdefault("vendor_model", binding.vendor_model or "")
            output.metadata.setdefault("key_prefix", binding.channel.audit_key_prefix())
            return self.postprocess(output)

        raise AllChannelsFailedError(f"{self.display_name} 所有通道均失败", cause=last_error)


__all__ = ["AIGCGenerationBase"]
