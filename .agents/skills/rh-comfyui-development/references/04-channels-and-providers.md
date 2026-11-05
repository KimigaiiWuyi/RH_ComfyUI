# 四、通道与供应商(负载均衡 / 熔断)

## 4.1 概念

- `ProviderChannel`(`core/channels/channel.py`):全模态通用的粗粒度通道
  抽象 —— 凭证管理 + 可用性 + `invoke()` 执行。
- `ChannelBinding(channel, vendor_model)`:模型 × 通道绑定;`vendor_model`
  是该通道下的厂商模型 ID(同一逻辑模型在不同家的 ID 不同)。
- 一个模型 `channel_bindings()` 返回多个绑定 = 自动参与负载均衡与熔断切换。
- `LocalChannel`:单执行路径模型(如 ComfyUI 本地)用,仅提供 name/weight
  让统计口径统一。
- `AdapterChannel`(`models/bridge.py`):把旧 Adapter 适配为 ProviderChannel。

与 `SeedanceProvider` 的关系:后者是视频领域细粒度(render/parse/poll)
抽象,保留;ProviderChannel 是全模态粗粒度抽象,可用薄包装适配。

## 4.2 写一个新通道

```python
class MyGatewayChannel(ProviderChannel):
    name = "my-gateway"
    weight = 5  # weighted 策略权重:官方直连高,代理低

    async def check_available(self) -> bool:
        # ★ 只读配置,禁止网络探测(路由阶段批量调用)
        from ...rh_config.comfyui_config import SERVICE_CONFIG

        return bool(SERVICE_CONFIG.get_config("my_gateway_key").data)

    async def unavailable_reason(self) -> str:
        return "未配置 my_gateway_key"

    async def invoke(self, **kwargs) -> NodeOutput:
        try:
            ...  # 调 utils/backends 里的客户端(POST 建单)
        except httpx.HTTPStatusError as e:
            raise ChannelError.from_http(
                e.response.status_code, stage="create",
                channel=self.name, message=f"上游拒绝: {e}",
            )
        except httpx.TransportError as e:
            raise ChannelError.from_transport(e, stage="create", channel=self.name)
```

**错误翻译是通道的义务**:上游异常必须翻译为 `ChannelError`,但**后端只报
事实**(HTTP 状态码 / 传输异常 / 出错阶段),`retryable` / `transient` /
换源 / 熔断全部由内核统一推导 —— **不要再手写这两个标志位**。

- HTTP 状态 → `ChannelError.from_http(status, stage=...)`,stage 取
  `create`(建单 POST)/`poll`(轮询 GET)/`result`(取件下载)/`other`。
  分类规则(HTTP 状态 → kind × phase)集中在
  `core/channels/failure.py::classify_http`:
  401/402/403 → AUTH_OR_QUOTA;408 → PROVIDER_FAULT;429 → RATE_LIMITED;
  其余 4xx → INVALID_REQUEST(不换源不计健康);≥500 → PROVIDER_FAULT。
  create 阶段 4xx 与 503 = REJECTED(确定没建单,strict 也换源),
  其余 5xx(500/502/504)= AMBIGUOUS(可能已建单,strict 不换源);
  poll/result 阶段一律 ACCEPTED(已有上游任务号,绝不重复建单)。
- 传输异常 → `ChannelError.from_transport(exc, stage=...)`:
  连不上(ConnectError/ConnectTimeout/PoolTimeout)= BEFORE_SEND;
  其他传输错误 create = AMBIGUOUS、poll/result = ACCEPTED。
- 语义错误显式声明 `kind`/`phase`:NO_TASK_ID → PROVIDER_FAULT+REJECTED;
  TASK_FAILED → TASK_FAILED+ACCEPTED;MEDIA_UPLOAD_FAILED / 素材不支持 →
  CAPABILITY+BEFORE_SEND;缺凭证 → AUTH_OR_QUOTA+BEFORE_SEND;
  参数类 → 不给 kind 的 `retryable=False`(推导 INVALID_REQUEST)。
- 429/503 仍是 transient(`retryable` 且原通道可退避),但现在是推导值:
  RATE_LIMITED 或 PROVIDER_FAULT+http_status=503 自动为 transient。
  有备选通道时 transient **立即换源**;仅单通道时才在原地指数退避
  (`transient_retry_*`,累计最长 3600s)。

## 4.3 给现有模型加一家供应商

在模型的 `channel_bindings()` 里追加绑定:

```python
def channel_bindings(self) -> list[ChannelBinding]:
    return [
        ChannelBinding(OfficialChannel(), vendor_model="doubao-seedance-2-0-260128"),
        ChannelBinding(MyGatewayChannel(), vendor_model="dreamina-seedance-2.0"),
    ]
```

Seedance 系列(`SeedanceVideoModel`)已改为多 ChannelBinding:
`channel_bindings()` 用 `builtin_seedance_channels()` 拼出 ark + runninghub,
`backend_models`(node_def)提供各家 vendor model id;加一家内置供应商就在
这里加一个 `SeedanceProviderChannel`。

`NodeDef.provider` 是**家族 id**(如 `ark` / `gateway` / `aifoundation`),
不是通道实例名。锁死家族时:`lock_bindings_to_provider_family`
(内置表命中只留该条;否则收 `name==家族` 或以 `{家族}_` 开头的外部通道)。
钉扎 `GenerationRequest.channel` 仍必须是 `channels[].name` 实例名
(如 `gateway_slot1_seedance`),传家族名 `gateway` 会 `ValidationError`。

### 4.3.1 外部插件为既有模型加一家供应商(通用扩展点)

不改开源仓库、给任意已注册模型追加一条通道:外部插件构造自己的
`ProviderChannel`(自带凭证面板),经 `channel_registry.register_binding()`
注入:

```python
from RH_ComfyUI.core import channel_registry

channel_registry.register_binding("gpt-image-2", MyAzureChannel())
```

所有桥接模型(`_PipelineBackedMixin.channel_bindings()`)都会自动并入
`channel_registry.bindings_for(self.name)`,与内置通道一起参与负载均衡。
通道自身的 `check_available()` 决定是否被选用(没填 key / 并发占满 →
返回 False 自动让路),`invoke()` 出错抛 `ChannelError(retryable=True)`
则切下一通道。

## 4.4 负载均衡与熔断(core/routing/balancer.py + core/channels/health.py)

- 健康状态机(`core/channels/health.py::ChannelHealth`)用两级 key:
  **group 级** `channel.health_group()`(共享同一上游端点的通道返回同值,
  默认 `self.name`;纯读配置、零网络)→ 吃 PROVIDER_FAULT / AUTH_OR_QUOTA;
  **member 级** `(模型, 通道)` → 吃 TASK_FAILED 与旧式无 kind 的 record_failure。
  RATE_LIMITED 只在 member 上写 15s `throttled_until`(降权不熔断);
  INVALID_REQUEST / CAPABILITY 不写任何健康。
- 状态:CLOSED →(连续失败 ≥ `Failure_Threshold`,0=不熔断)→ OPEN,
  冷却 `min(base·2^opens, max) ±20%` 抖动(base=`Circuit_Base_Cooldown` 30s,
  max=`Circuit_Max_Cooldown` 1800s;AUTH_OR_QUOTA 起步 ×20);
  到期 → HALF_OPEN 只放一个探测(lease 900s 自动释放);
  探测成功 → CLOSED 清零,失败 → 立即重 OPEN 且冷却翻倍。
- `order_candidates(strict=...)` 排序 = [可探测的 HALF_OPEN(strict 下仅当
  上次失败为 BEFORE_SEND/REJECTED 才排前)] + CLOSED(按策略) + 节流中 +
  OPEN(按到期升序兜底)+ 不可探测的 HALF_OPEN;全灭兜底语义不变。
- `begin_attempt()` 在 HALF_OPEN 时原子占探测令牌,被占且还有备选 → 返回
  None 先跳过(主循环末兜底再试);每次尝试恰以
  record_success / record_failure / end_attempt 之一收尾(finally 保证)。
- `health_snapshot()` 保留旧键 `{scope}/{member}` → `failure_count` /
  `circuit_open`,新增 `state` / `cooldown_remaining` / `last_kind` /
  `last_phase` / `opens` 及 `group:<名>` 条目。
- 策略与阈值读 PLUGIN_CONFIG 通用键 `Load_Balance_Mode` / `Failure_Threshold` /
  `Circuit_Base_Cooldown` / `Circuit_Max_Cooldown`,每次决策实时读取。
- `run()` 先写健康再决策(strict 也记):INVALID_REQUEST 不记;参数类
  (`retryable=False`)不换源直接抛 —— 与旧语义一致。
- **请求钉扎**:`GenerationRequest.channel`(或 `params.channel`)指定
  `GET /models` 的 `channels[].name`。空 / `auto` = 上表负载均衡。
  指定名称则只走该通道名(同名多路仍可互切),不切到其它名称;
  名称不在该模型声明列表、或当前 `check_available()` 为 False
  → `ValidationError`(dispatcher 在扣费前拦截)。
  公开函数:`api.resolve_channel_pin(model, channel)` 供调用方预扣前校验。

## 4.5 轮询型上游

"创建任务 → 轮询终态"型上游用 `PollingChannelMixin`
(`core/channels/polling.py`)提供的通用骨架,不要自己写 while+sleep。
模型侧把 `execution_mode` 声明为 `async_poll`(HTTP 清单会透出,
调用方据此展示进度条)。

### 4.5.1 网络抖动重试(与模型无关)

上游 HTTP 的 **ReadError / ConnectError / 超时** 等传输层失败,在发出请求的
那一层等 **5 秒再发同一请求**,共 **5 次**;五次都失败才向上抛
`POLL_NETWORK_ERROR` / `PROVIDER_NETWORK_ERROR`。实现:

- `utils/backends/http_retry.py`:`RetryingAsyncClient` / `call_with_network_retry`
- Seedance / HappyHorse / MiniMax H3 的 `_get_client()` 已换成该 client,
  网关 Seedance GET 轮询走同一路径
- **不要**在 `poll_until_done` 再套一层同样的 5×5s(会变成 25 次)

HTTP 4xx/5xx **响应**不是网络抖动,不走这层;轮询 GET 的 408/429/5xx
由 `PollErrorTolerance`(`core/channels/polling.py`)在窗口内(默认 600s)
退避吸收 —— 上游已受理的任务不再因轮询抖动被判失败或重复建单;
窗口耗尽后抛原有错误码(phase=ACCEPTED,推导 retryable=False)。
