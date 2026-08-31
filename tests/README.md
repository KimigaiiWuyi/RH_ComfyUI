# tests — 内核单元测试(全部离线,零网络)

```
python -m pytest tests/ -q      # 在插件根目录执行
```

| 文件 | 覆盖 |
|---|---|
| `conftest.py` | 把 gsuid_core 与插件根加入 sys.path |
| `test_balancer.py` | 负载均衡选择、熔断触发与冷却恢复 |
| `test_registry.py` | ModelRegistry 注册/去重/按模态查询 |
| `test_schema_validation.py` | PortSpec 通用校验 + Seedance/Wan 跨字段约束(多参考上限、分辨率约束等) |
| `test_dispatcher_billing.py` | dispatch 计费路径:成功 settle / 校验失败不扣费 / 失败退款幂等 / BaseException 退款 / estimate_cost / settle_cost 差额对齐 / 超时预算 |
| `test_wallet_operation_transactions.py` | 钱包回执事务:临时 SQLite 沙箱、幂等/回滚/并发/不可变触发器,不碰生产 GsData.db |
| `test_seedance_usage_reconcile.py` | 从厂商 raw / 截断 JSON 解析 token,按有输入费率回算积分 |
| `test_channel_failover.py` | 错误翻译、多通道故障切换、非重试不计熔断、transient(429/503)原通道最长 1h 排队 |
| `test_seedance_channel.py` | SeedanceProviderChannel 凭证热更新、异常翻译、Dry-Run 透传 |
| `test_gemini_image.py` | Gemini 双模判定、steps 图片提取、banana2 接线 |
| `test_openai_image.py` | 供应商池配置解析(含 weight)、sync/resync、/images/edits 端点分流 |
| `test_channel_resync.py` | 通道重绑钩子、`set_config` 监视、失败隔离 |
| `test_ensure_standard_image.py` | 参考图一律 JPEG 归一、透明铺灰底、Gemini mime |
| `test_model_catalog.py` | /models 可用性以通道为准、纯 ABC 模型进清单 |
| `test_model_schema.py` | 各模型 input_schema 与能力声明一致性 |
| `test_http_contract.py` | HTTP 契约快照(golden test),字段只增不改的 CI 强制 |
| `test_concurrency_reload.py` | 并发闸热更新与非法值回落 |
| `test_provider_stats.py` | 供应商对账命令渲染 |
| `test_video_normalize.py` | 视频预处理下沉到 normalize() |

## 约定

- 用 FakeModel + FakePolicy,不 mock 网络 —— 内核本身不应有网络调用
  (`test_openai_image.py` 用假 ClientSession 断言端点/协议形状,不发包);
- 统计落库在单测里 monkeypatch 静音(`_mute_recording`);
- 改动 `core/` 任何文件,提交前必须全绿。
