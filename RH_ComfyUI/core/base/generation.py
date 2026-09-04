"""AIGCGenerationBase — 所有 AIGC 生成模型的顶层抽象基类

生命周期(模板方法 run() 固定,子类覆盖钩子):

    run(request)
      ├─ 1. validate(request)            # schema 通用校验 + 子类跨字段校验
      ├─ 2. normalize(request)           # 默认值填充 / 单位归一化(可覆盖)
      ├─ 3. plugin_dry_run() 则抛 DryRunInterrupt
      ├─ 4. 钉扎 request.channel(空/auto=负载均衡;未知名称 ValidationError)
      ├─ 5. balancer.order_candidates()  # 负载均衡选通道(多通道时)
      ├─ 6. execute_on_channel(...)      # ★ 子类核心:组装请求并执行
      │     ├─ 瞬时失败(transient,如 429/503)→ 原通道指数退避排队
      │     │   (最长 transient_retry_max_wait=1h;超时放弃该通道)
      │     └─ 失败且可重试 → 记熔断 → 换下一个通道
      └─ 7. postprocess(output)          # 输出归一化(可覆盖)

设计约束:
- 本类不做计费、不做统计、不做全局限流 —— 那是 dispatcher 的职责。
- 本类不持有任何 HTTP 细节 —— 通信永远委托 ProviderChannel / backends 客户端。
"""

from __future__ import annotations

import time
import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, Optional

from gsuid_core.logger import logger

from .errors import ChannelError, DryRunInterrupt, ValidationError, AllChannelsFailedError
from ..schema.card import ModelCard
from ..schema.types import PortSpec, PortType, NodeOutput, ProgressCallback
from ..schema.request import TaskType, GenerationRequest
from ..channels.channel import ChannelBinding

if TYPE_CHECKING:
    # 运行时延迟导入,避免 base ↔ routing 的循环(registry 依赖本模块)
    from ..routing.balancer import LoadBalancer
    from ...utils.core.pipeline import NodeDef


def normalize_channel_pin(raw: object) -> str | None:
    """空 / auto → None(由负载均衡分配);其它非空字符串原样(去首尾空白)返回。"""
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not name or name.lower() == "auto":
        return None
    return name


def requested_channel_name(request: GenerationRequest) -> str | None:
    """读取调用方钉扎的通道名。顶层 ``channel`` 优先,其次 ``params.channel``。"""
    candidates: list[object] = [request.channel]
    params = request.params
    if isinstance(params, dict) and "channel" in params:
        candidates.append(params["channel"])
    for raw in candidates:
        pin = normalize_channel_pin(raw)
        if pin is not None:
            return pin
    return None


def _unique_channel_names(bindings: list[ChannelBinding]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for b in bindings:
        n = b.channel.name
        if n in seen:
            continue
        seen.add(n)
        names.append(n)
    return names


def _unknown_channel_message(display_name: str, pin: str, bindings: list[ChannelBinding]) -> str:
    names = _unique_channel_names(bindings)
    if names:
        return f"{display_name} 没有通道 {pin!r},可选: {'、'.join(names)}"
    return f"{display_name} 没有通道 {pin!r}(该模型未声明任何通道)"


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

    # ── 弹性:瞬时错误(429/503)原通道排队退避 ──
    # 初始间隔 → 指数增长,单次上限 max_delay;累计等待超过 max_wait 则放弃该通道
    # (再 failover / 整单失败)。不计熔断(通道健康,只是瞬时过载)。
    transient_retry_delay: ClassVar[float] = 2.0
    transient_retry_max_delay: ClassVar[float] = 60.0
    transient_retry_max_wait: ClassVar[float] = 3600.0  # 1 小时

    # ── 路由 ──
    priority: int = 50  # 数字越大越优先
    execution_mode: ClassVar[str] = "sync"  # sync / async_poll

    # ── 取消能力 ──
    # supports_cancel:进行中任务可被 cancel_generation 取消(本地 asyncio.Task.cancel)。
    # 所有经 dispatch() 的模型默认 True;rh_app 等例外覆写 False。
    supports_cancel: ClassVar[bool] = True
    # supports_remote_cancel:**已降级为软提示**,目录真实值以通道/供应商为准
    # (ProviderChannel.supports_remote_cancel)。未知供应商默认 False。
    supports_remote_cancel: ClassVar[bool] = False

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

    async def prepare_request(self, request: GenerationRequest) -> GenerationRequest:
        """异步预处理钩子(下载/重编码参考视频、放大过小参考图等 IO 操作)。

        在 ``normalize`` 之后、通道选择之前调用。默认原样返回。
        Seedance 等需要钳参考视频时长 / 放大参考图短边的模型覆盖本方法。
        """
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

    def settle_cost(self, request: GenerationRequest, usage: dict) -> Optional[int]:
        """成功后按供应商用量计算实扣积分;None = 维持预扣。

        dispatcher 在 ``model.run()`` 成功后调用,把返回值交给
        ``BillingPolicy.settle(reservation, actual)`` 做差额对齐
        (actual>预扣则补扣,actual<预扣则退差;禁止按 actual 再全额扣一次)。
        必须是纯函数:只读 request/usage,不做 IO。usage 无法换算时返回 None。
        """
        return None

    def point_range(self) -> tuple[int, int]:
        """该模型单次请求的积分范围(min, max)。

        供前端在模型选择列表中展示"最低~最高积分"用。
        默认返回(point_cost, point_cost)(静态计费)。
        动态计费的模型覆盖本方法,返回基于参数范围的估算最小值与最大值。
        返回值必须是纯函数:不做 IO,不抛业务异常。
        """
        return (self.point_cost, self.point_cost)

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

    def bindings_for_request(self, request: GenerationRequest) -> list[ChannelBinding]:
        """按请求钉扎过滤通道。未钉扎返回全部;名称不在声明列表则 ValidationError。"""
        bindings = self.channel_bindings()
        pin = requested_channel_name(request)
        if pin is None:
            return bindings
        named = [b for b in bindings if b.channel.name == pin]
        if not named:
            raise ValidationError(_unknown_channel_message(self.display_name, pin, bindings))
        return named

    async def ensure_channel_pin(self, request: GenerationRequest) -> None:
        """钉扎通道在扣费前校验:未知名称或不存在可用实例 → ValidationError。

        auto 直接返回。``check_available`` 只读配置,禁止网络探测。
        """
        pin = requested_channel_name(request)
        if pin is None:
            return
        named = self.bindings_for_request(request)
        for b in named:
            if await b.channel.check_available():
                return
        reason = await named[0].channel.unavailable_reason()
        raise ValidationError(f"通道 {pin} 当前不可用: {reason}")

    # ═════════════════ 模板方法(不建议覆盖) ═════════════════

    async def run(
        self,
        request: GenerationRequest,
        *,
        on_progress: Optional[ProgressCallback] = None,
    ) -> NodeOutput:
        """统一生命周期:校验 → 归一化 → 选通道 → 执行(带故障切换) → 后处理

        通道选择:
        0. **钉扎** —— ``request.channel`` 非空且非 auto 时只保留该通道名
           (同名多路仍可互切;不会切到其它名称)。未知名称 → ValidationError。
        1. **能力预过滤** —— 排除掉 ``supports_request()`` 返回 False
           的通道(典型:seedance2 模型同时挂 ark / 网关 / aifoundation,
           用户传 1080P 时 aifoundation 仅支持 720P,应在此被剔除,免得
           LB 投到它头上被 ``validate_spec`` 抛 ``UNSUPPORTED_RESOLUTION``
           后 ``retryable=False`` 整单失败)。
        2. **LoadBalancer 排序** —— 只在有能力承接的候选里 round_robin /
           weighted / least_failures;熔断统计同理只看子集。
        """
        self.validate(request)
        request = self.normalize(request)
        from ...rh_config.comfyui_config import plugin_dry_run

        if plugin_dry_run():
            logger.info(f"[Dry-Run] 拦截 {self.name} ({self.display_name}) prompt={(request.prompt or '')[:80]!r}")
            raise DryRunInterrupt(f"[Dry-Run] 已启用,未发送上游请求: {self.name}")
        request = await self.prepare_request(request)

        declared = self.channel_bindings()
        if not declared:
            raise ChannelError(f"{self.display_name} 未声明任何执行通道")
        bindings = self.bindings_for_request(request)
        pin = requested_channel_name(request)

        # ── 能力预过滤(2026-07-20 加入) ──
        capable = [b for b in bindings if b.channel.supports_request(request)]
        if not capable:
            # 全部通道都因能力不兼容被排除 → 抛参数类错误。
            # dispatcher 走 refund 路径,积分退掉;与 model.validate() 异常同语义。
            # 之所以用 ValidationError 而非 ChannelError:这是请求参数与通道能力
            # 的不匹配,跟「channel 执行失败」是两件事 —— 后者交给 ChannelError
            # 让模板方法走「切换下一通道」逻辑,前者直接抛、不再尝试。
            lines: list[str] = []
            for b in bindings:
                lines.append(f"- {b.channel.name}: {await b.channel.unavailable_reason()}")
            if pin is not None:
                raise ValidationError(
                    f"{self.display_name} 的通道 {pin} 无法处理该请求"
                    f"(分辨率/宽高比/时长可能不在该通道能力范围内)"
                    f"\n通道反馈:\n" + "\n".join(lines)
                )
            raise ValidationError(
                f"{self.display_name} 的所有通道均无法处理该请求"
                f"(分辨率/宽高比/时长可能不在任一通道能力范围内)"
                f"\n通道反馈:\n" + "\n".join(lines)
            )
        bindings = capable

        ordered = self.balancer().order_candidates(scope=self.name, candidates=bindings)

        from ..dispatch.concurrency import channel_slot, channel_has_capacity, channel_slot_for_model

        # 供应商软排序:满载(在途数 ≥ 并发上限)的通道排到末尾,优先溢到空闲供应商,
        # 避免阻塞在繁忙供应商的信号量上;组内保持负载均衡给出的顺序
        if len(ordered) > 1:
            free = [b for b in ordered if channel_has_capacity(b.channel.name)]
            if free:
                ordered = free + [b for b in ordered if b not in free]

        last_error: Optional[Exception] = None
        # 按 binding 身份记可用候选,不能用 channel.name:同名两路(内置 gemini +
        # 外部也叫 gemini)失败一路会把另一路从集合里一起删掉,再也切不过去。
        available: list[ChannelBinding] = []
        skipped_notes: list[str] = []
        for b in ordered:
            if await b.channel.check_available():
                available.append(b)
            else:
                reason = await b.channel.unavailable_reason()
                skipped_notes.append(f"{b.channel.name}({reason})")
                logger.info(f"[{self.name}] 跳过不可用通道 {b.channel.name}: {reason}")

        if pin is not None and not available:
            skipped = "; ".join(skipped_notes) if skipped_notes else "不可用"
            raise ValidationError(f"通道 {pin} 当前不可用: {skipped}")

        for binding in ordered:
            if not any(b is binding for b in available):
                if not await binding.channel.check_available():
                    continue
                available.append(binding)
            output = None
            # 本通道 429/503 排队窗口:从首次 transient 起算,超时放弃该通道
            transient_started_at: Optional[float] = None
            transient_attempt = 0
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
                    from ...utils.backends.http_retry import is_strict_create_once

                    if is_strict_create_once():
                        raise
                    if not e.retryable:
                        # 参数类失败(换通道也没用)不计入熔断:通道本身是健康的,
                        # 用户反复提交坏请求不应把通道推入冷却期
                        raise
                    if e.transient:
                        now = time.monotonic()
                        if transient_started_at is None:
                            transient_started_at = now
                        elapsed = now - transient_started_at
                        max_wait = float(self.transient_retry_max_wait)
                        if elapsed < max_wait:
                            # 指数退避:delay * 2^(n-1),封顶 max_delay,且不超过剩余预算
                            transient_attempt += 1
                            base = float(self.transient_retry_delay)
                            cap = float(self.transient_retry_max_delay)
                            delay = min(base * (2 ** (transient_attempt - 1)), cap, max_wait - elapsed)
                            delay = max(0.0, delay)
                            remain = max_wait - elapsed
                            logger.warning(
                                f"[{self.name}] 通道 {binding.channel.name} 瞬时限流({e}),"
                                f"排队第 {transient_attempt} 次,"
                                f"{delay:.1f}s 后原通道重试"
                                f"(已等 {elapsed:.0f}s / 上限 {max_wait:.0f}s,剩余 {remain:.0f}s)"
                            )
                            await self._emit_transient_progress(
                                on_progress,
                                channel=binding.channel.name,
                                attempt=transient_attempt,
                                delay=delay,
                                elapsed=elapsed,
                                max_wait=max_wait,
                            )
                            if delay > 0:
                                await asyncio.sleep(delay)
                            continue
                        # 排队预算耗尽:记失败并切通道/整单失败(任务移除由 dispatcher 退款落库)
                        logger.error(
                            f"[{self.name}] 通道 {binding.channel.name} 429/503 排队已达上限 {max_wait:.0f}s,放弃该通道"
                        )
                        last_error = ChannelError(
                            f"{binding.channel.name} 限流排队超过 {max_wait:.0f}s: {e}",
                            retryable=True,
                            transient=False,
                            channel=binding.channel.name,
                            code="TRANSIENT_QUEUE_TIMEOUT",
                            user_message=(f"上游繁忙,排队已超过 {int(max_wait // 60)} 分钟仍未恢复,任务已取消。"),
                        )
                    self.balancer().record_failure(scope=self.name, member=binding.channel.name)
                    available = [b for b in available if b is not binding]
                    remain = ", ".join(b.channel.name for b in available)
                    if available:
                        logger.warning(
                            f"[{self.name}] 通道 {binding.channel.name} 失败({last_error}),切换下一通道(剩余 {remain})"
                        )
                    else:
                        skipped = f"; 未尝试: {', '.join(skipped_notes)}" if skipped_notes else ""
                        logger.warning(
                            f"[{self.name}] 通道 {binding.channel.name} 失败({last_error}),无更多可用通道{skipped}"
                        )
                    break
            if output is None:
                continue
            self.balancer().record_success(scope=self.name, member=binding.channel.name)
            output.metadata.setdefault("channel", binding.channel.name)
            output.metadata.setdefault("vendor_model", binding.vendor_model or "")
            output.metadata.setdefault("key_prefix", binding.channel.audit_key_prefix())
            return self.postprocess(output)

        skipped = f"; 未尝试: {', '.join(skipped_notes)}" if skipped_notes else ""
        raise AllChannelsFailedError(
            f"{self.display_name} 所有通道均失败{skipped}",
            cause=last_error,
        )

    async def _emit_transient_progress(
        self,
        on_progress: Optional[ProgressCallback],
        *,
        channel: str,
        attempt: int,
        delay: float,
        elapsed: float,
        max_wait: float,
    ) -> None:
        """429 排队时向入口播报进度(可选;失败不影响主流程)。"""
        if on_progress is None:
            return
        try:
            from ...utils.core.types import ProgressEvent

            percent = min(90.0, 10.0 + (elapsed / max_wait) * 80.0) if max_wait > 0 else 10.0
            event = ProgressEvent(
                stage="queued",
                percent=percent,
                message=(
                    f"上游限流排队中({channel}) 第 {attempt} 次,"
                    f"{delay:.0f}s 后重试 · 已等 {elapsed:.0f}s/{max_wait:.0f}s"
                ),
            )
            result = on_progress(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 - 进度播报失败不影响排队主流程
            pass


__all__ = [
    "AIGCGenerationBase",
    "normalize_channel_pin",
    "requested_channel_name",
]
