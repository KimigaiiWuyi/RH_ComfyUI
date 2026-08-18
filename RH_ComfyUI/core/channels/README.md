# core/channels — 供应商通道抽象

| 文件 | 内容 |
|---|---|
| `channel.py` | `ProviderChannel`(全模态通用的粗粒度通道 ABC:`check_available` / `unavailable_reason` / `invoke`)、`ChannelBinding`(通道 + vendor_model + 权重)、`LocalChannel`(本地/测试用) |
| `polling.py` | `PollingChannelMixin` — "创建任务 → 轮询终态"型上游的通用骨架(自 seedance provider 提炼泛化) |
| `registry.py` | `channel_registry` — 外部插件为既有模型追加通道 |
| `resync.py` | `register_resync_hook` / `bind_config_resync` — 配置改完按快照重挂绑定 |

## 与旧 SeedanceProvider 的关系

`SeedanceProvider` 是视频领域细粒度(render/parse/poll)抽象,**保留不动**;
`ProviderChannel` 是全模态粗粒度抽象,模型侧可用薄包装把前者适配成后者
(参见 `models/bridge.py` 的 `AdapterChannel`)。

## 维护须知

- 通道的 `check_available()` 只读配置;真实连通性问题在 `invoke()` 抛
  `ChannelError(retryable=...)`,由负载均衡熔断处理;
- 同一模型多通道时,每个 `ChannelBinding` 是独立的熔断/权重单元。
