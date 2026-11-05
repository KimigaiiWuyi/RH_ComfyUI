# core/channels — 供应商通道抽象

| 文件 | 内容 |
|---|---|
| `channel.py` | `ProviderChannel`(全模态通用的粗粒度通道 ABC:`check_available` / `unavailable_reason` / `invoke`)、`ChannelBinding`(通道 + vendor_model + 权重)、`LocalChannel`(本地/测试用) |
| `family.py` | `NodeDef.provider` 家族过滤:`channel_name_in_family` / `lock_bindings_to_provider_family` |
| `polling.py` | `PollingChannelMixin` — "创建任务 → 轮询终态"型上游的通用骨架(自 seedance provider 提炼泛化);`PollErrorTolerance` — 轮询 GET 的 408/429/5xx 窗口退避 |
| `failure.py` | `FailureKind`/`Phase`/`Stage` + `classify_http`/`classify_transport` — 唯一的错误分类入口;后端只报事实,不手写 retryable/transient |
| `health.py` | `ChannelHealth` — group/member 两级健康与 CLOSED→OPEN→HALF_OPEN 状态机(指数冷却+单探测+抖动) |
| `registry.py` | `channel_registry` — 外部插件为既有模型追加通道 |
| `resync.py` | `register_resync_hook` / `bind_config_resync` — 配置改完按快照重挂绑定 |

## 与旧 SeedanceProvider 的关系

`SeedanceProvider` 是视频领域细粒度(render/parse/poll)抽象,**保留不动**;
`ProviderChannel` 是全模态粗粒度抽象,模型侧可用薄包装把前者适配成后者
(参见 `models/bridge.py` 的 `AdapterChannel`)。

## 维护须知

- 通道的 `check_available()` 只读配置;真实连通性问题在 `invoke()` 抛
  `ChannelError`(HTTP 用 `from_http`、传输用 `from_transport`,标注 stage);
- 共享同一上游端点的通道应覆盖 `health_group()` 返回同一值(如 base_url
  的 host),让 group 级熔断跨模型生效;只读配置、零网络;
- 同一模型多通道时,每个 `ChannelBinding` 是独立的熔断/权重单元;
  group 级熔断按 `health_group()` 聚合。
