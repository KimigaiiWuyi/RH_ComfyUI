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
    weight = 5                     # weighted 策略权重:官方直连高,代理低

    async def check_available(self) -> bool:
        # ★ 只读配置,禁止网络探测(路由阶段批量调用)
        from ...rh_config.comfyui_config import SERVICE_CONFIG
        return bool(SERVICE_CONFIG.get_config("my_gateway_key").data)

    async def unavailable_reason(self) -> str:
        return "未配置 my_gateway_key"

    async def invoke(self, **kwargs) -> NodeOutput:
        try:
            ...  # 调 utils/backends 里的客户端
        except TimeoutError as e:
            raise ChannelError(f"网关超时: {e}", retryable=True)   # 可切下一通道
        except UpstreamRejectedError as e:
            raise ChannelError(f"参数被拒: {e}", retryable=False)  # 直接透传用户
```

**错误翻译是通道的义务**:上游异常必须翻译为 `ChannelError`,
`retryable` 决定 run() 是否切换下一通道并记熔断;参数类错误(换通道也没用)
必须 `retryable=False`,避免无谓重试烧钱。

## 4.3 给现有模型加一家供应商

在模型的 `channel_bindings()` 里追加绑定:

```python
def channel_bindings(self) -> list[ChannelBinding]:
    return [
        ChannelBinding(OfficialChannel(), vendor_model="doubao-seedance-2-0-260128"),
        ChannelBinding(MyGatewayChannel(), vendor_model="dreamina-seedance-2.0"),
    ]
```

对桥接模型(defs.py 里的类):Seedance 系列的多供应商仍由 Adapter 内部
处理(`backend_models` 字段声明各家的模型 ID:ark/gateway/runninghub),
在 `node_def()` 的 `backend_models` 里加一项即可;全新模型建议直接用
多 ChannelBinding 方式。

## 4.4 负载均衡与熔断(core/routing/balancer.py)

- `LoadBalancer` 按 `(scope, member)` 二级 key 记状态,scope=模型名,
  member=通道名;同一模型的多个通道共享一套熔断计数;
- `order_candidates()` 按策略(优先级/权重/随机)排序候选;
- `record_failure()` 达到阈值触发熔断,冷却期内该通道被排到最后/跳过;
  `record_success()` 恢复;
- 策略与阈值读 `PLUGIN_CONFIG` 通用键;`Seedance_*` 旧键仅迁移期兜底,
  **不要新增依赖**;
- 熔断只由 `ChannelError(retryable=True)` 触发,业务校验错误不影响通道健康度。

## 4.5 轮询型上游

"创建任务 → 轮询终态"型上游用 `PollingChannelMixin`
(`core/channels/polling.py`)提供的通用骨架,不要自己写 while+sleep。
模型侧把 `execution_mode` 声明为 `async_poll`(HTTP 清单会透出,
画布据此展示进度条)。
