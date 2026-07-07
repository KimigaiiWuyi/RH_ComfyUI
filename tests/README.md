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
| `test_dispatcher_billing.py` | dispatch 三条计费路径:成功 commit / 校验失败不扣费 / 执行失败退款幂等 |

## 约定

- 用 FakeModel + FakePolicy,不 mock 网络 —— 内核本身不应有网络调用;
- 统计落库在单测里 monkeypatch 静音(`_mute_recording`);
- 改动 `core/` 任何文件,提交前必须全绿。
