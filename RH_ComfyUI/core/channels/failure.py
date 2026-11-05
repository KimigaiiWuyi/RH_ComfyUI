"""失败分类 — 通道只上报事实(kind/phase),换源与熔断由 run() 唯一决策

kind 回答「换一家通道有没有用 / 该不该记健康」:
- INVALID_REQUEST : 参数/审核类。不换源、不计健康。
- CAPABILITY      : 本通道不支持此形态,别家可能行。换源、不计健康。
- RATE_LIMITED    : 429/队列满。换源;无备选时原地退避;只打短节流标记。
- PROVIDER_FAULT   : 5xx/408/网络/空回包/无任务号。换源 + 计 group 健康。
- AUTH_OR_QUOTA   : 401/402/403/余额不足/缺 key。换源 + 计 group 健康(长冷却)。
- TASK_FAILED     : 上游受理后明确报失败。可换源(非 strict),计 member 健康。

phase 回答「出错时写请求落到哪一步」:
- BEFORE_SEND : 请求没发出(连不上/池超时/本地素材准备失败/缺 key)。
- REJECTED    : 上游明确回错且确定没建单。
- AMBIGUOUS   : 写请求已发出但结果不明。strict 下禁止换源(防重复建单)。
- ACCEPTED    : 已有上游任务号(轮询/取件阶段)。禁止换源(任务可能已成功)。
- UNKNOWN     : 旧式构造未声明;strict 下按 AMBIGUOUS 处理。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Callable

Stage = Literal["create", "poll", "result", "other"]


class FailureKind(str, Enum):
    INVALID_REQUEST = "invalid_request"
    CAPABILITY = "capability"
    RATE_LIMITED = "rate_limited"
    PROVIDER_FAULT = "provider_fault"
    AUTH_OR_QUOTA = "auth_or_quota"
    TASK_FAILED = "task_failed"


class Phase(str, Enum):
    UNKNOWN = "unknown"
    BEFORE_SEND = "before_send"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"
    ACCEPTED = "accepted"


# strict 下允许换源的 phase:确定没建单,重发不重复计费
FAILOVER_SAFE_PHASES: frozenset[Phase] = frozenset({Phase.BEFORE_SEND, Phase.REJECTED})

# 成员级短节流时长(不进入失败计数)
RATE_LIMIT_THROTTLE_S = 15.0


def classify_http_status(status: int, *, stage: Stage) -> tuple[FailureKind, Phase]:
    """HTTP 状态码 + 出错阶段 → (kind, phase)。

    与旧 ``http_status_retryable`` 等价口径:返回 True 的码 ⇔ kind != INVALID_REQUEST;
    402 归入 AUTH_OR_QUOTA(旧表为 False,属本次有意的口径调整)。
    """
    if status in (401, 402, 403):
        kind = FailureKind.AUTH_OR_QUOTA
    elif status == 408:
        kind = FailureKind.PROVIDER_FAULT
    elif status == 429:
        kind = FailureKind.RATE_LIMITED
    elif status >= 500:
        kind = FailureKind.PROVIDER_FAULT
    else:
        kind = FailureKind.INVALID_REQUEST

    if stage == "create":
        # 4xx 与 503 是明确拒单;500/502/504 等已发出但结果不明
        phase = Phase.REJECTED if status < 500 or status == 503 else Phase.AMBIGUOUS
    elif stage in ("poll", "result"):
        phase = Phase.ACCEPTED
    else:
        phase = Phase.UNKNOWN
    return kind, phase


def classify_transport(exc: BaseException, *, stage: Stage) -> "tuple[FailureKind, Phase]":
    """传输异常 → (kind, phase);kind 恒为 PROVIDER_FAULT。

    沿 ``__cause__`` / ``__context__`` 链判定:
    连不上(ConnectError/ConnectTimeout/PoolTimeout/ClientConnectorError/
    ConnectionRefusedError)说明请求没发出 → BEFORE_SEND;其余传输错误在
    create 阶段结果不明 → AMBIGUOUS;轮询/取件 → ACCEPTED。
    """
    before_send, transport = _transport_predicates()
    saw_transport = False
    cur: BaseException | None = exc
    seen: set[int] = set()
    depth = 0
    phase = Phase.UNKNOWN
    while cur is not None and id(cur) not in seen and depth < 8:
        seen.add(id(cur))
        depth += 1
        if before_send(cur):
            return FailureKind.PROVIDER_FAULT, Phase.BEFORE_SEND
        if transport(cur):
            saw_transport = True
        cur = cur.__cause__ or cur.__context__
    if saw_transport:
        if stage == "create":
            phase = Phase.AMBIGUOUS
        elif stage in ("poll", "result"):
            phase = Phase.ACCEPTED
    return FailureKind.PROVIDER_FAULT, phase


def _transport_predicates() -> "tuple[Callable[[BaseException], bool], Callable[[BaseException], bool]]":
    """返回 (is_before_send, is_transport_error) 两个判定函数;aiohttp 可选。"""
    import asyncio

    import httpx

    aio_connector: tuple[type[BaseException], ...] = ()
    aio_client: tuple[type[BaseException], ...] = ()
    try:
        import aiohttp

        aio_connector = (aiohttp.ClientConnectorError,)
        aio_client = (aiohttp.ClientConnectionError, aiohttp.ServerTimeoutError, aiohttp.ClientPayloadError)
    except ImportError:
        pass

    def _before_send(exc: BaseException) -> bool:
        return isinstance(
            exc,
            (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, ConnectionRefusedError) + aio_connector,
        )

    def _transport(exc: BaseException) -> bool:
        return isinstance(exc, (httpx.TransportError, asyncio.TimeoutError, OSError) + aio_client)

    return _before_send, _transport


def classify_http(status: int, *, stage: Stage) -> tuple[FailureKind, Phase]:
    """``classify_http_status`` 的规范名(薄别名)。"""
    return classify_http_status(status, stage=stage)


# 规范名别名:FailurePhase 与 Phase 同义
FailurePhase = Phase


def http_status_retryable(status: int) -> bool:
    """旧策略薄封装:换通道是否可能解决。402 起归入 AUTH(与旧表不同,见上)。"""
    return classify_http_status(status, stage="other")[0] is not FailureKind.INVALID_REQUEST


__all__ = [
    "FailureKind",
    "Phase",
    "FailurePhase",
    "Stage",
    "FAILOVER_SAFE_PHASES",
    "RATE_LIMIT_THROTTLE_S",
    "classify_http",
    "classify_http_status",
    "classify_transport",
    "http_status_retryable",
]
