# 八、测试、代码红线与上线自查

## 8.1 测试

```bash
# 在插件根目录执行
python -m pytest tests/ -q     # 全离线,秒级,必须全绿
ruff check RH_ComfyUI          # 风格检查(120 列,按显示宽度计 CJK)
```

| 测试文件 | 覆盖 |
|---|---|
| `test_balancer.py` | 负载均衡选择、熔断触发与冷却恢复 |
| `test_registry.py` | ModelRegistry 注册/去重/按模态查询 |
| `test_schema_validation.py` | PortSpec 通用校验 + Seedance/Wan 跨字段约束 |
| `test_dispatcher_billing.py` | 成功 commit / 校验失败不扣费 / 失败退款幂等 |

约定:用 FakeModel + FakePolicy,不 mock 网络(内核本身不应有网络调用);
统计落库用 `_mute_recording` monkeypatch 静音。
新增跨字段校验的模型必须在 `test_schema_validation.py` 补用例。

## 8.2 代码红线(违反必被打回)

1. **不绕过 `dispatch()`** 直接调 `model.run()`(计费统计会丢);
2. **`validate()` / `check_available()` 零副作用零网络**;
3. **校验先于扣费**的顺序不许动;dispatcher 失败路径"先落统计再退款"的
   顺序不许动;
4. **HTTP 契约只增不改**(`/RH_ComfyUI/models*` 与 `api.submit` 签名);
5. **`core/` 不 import 上层**(models/、utils/、入口包);
6. **开源仓库零闭源内容**(URL/凭证/条件 import/enterprise 分支);
7. 模型 `name` 是主键,改名 = 下线旧 + 上线新(统计断档),慎改;
8. `RHComfyuiTaskRecord` 既有列不改名不删;新列必须带默认值;
9. 每个新目录必须有 README.md(目录职责 + 维护须知);
10. 遵守 `E:\MyPyProject\gsuid_core\docs\LLM.md` 的框架级开发规范。

## 8.3 上线自查清单

- [ ] `pytest tests/ -q` 全绿;`ruff check` 无告警;
- [ ] 启动冒烟:`discover_builtin_models()` 日志确认模型数正确、新模型在列;
- [ ] `rh 模型列表` / `GET /RH_ComfyUI/models` 能看到新模型且 `available` 正确;
- [ ] 缺配置时 `unavailable_reason` 是人话;
- [ ] `knowledge_content` 写清优势/适用/不适用(Agent 选型依据);
- [ ] 真实生成一次:`RHComfyuiTaskRecord` 有记录且 point_cost / entry_point /
      channel 正确;
- [ ] 人为造一次失败(如错误参数):确认不扣费;造一次执行失败:确认退款;
- [ ] 多通道模型:关掉主通道配置,确认自动切换备用通道;
- [ ] 文档同步:模型能力变化 → `knowledge_content` + 相关 README;
      架构变化 → `docs/ABC_ARCHITECTURE.md` + 本 SKILL。

## 8.4 常见故障定位

| 症状 | 先查 |
|---|---|
| 新模型三入口都看不到 | 是否加进 `ALL_MODELS`;启动日志有无注册行 |
| 模型显示不可用 | `required_config` / `requirements` 对应配置键是否已配 |
| 扣了积分没产物 | 统计表该记录 status 与退款标记(见 05 章 5.1) |
| 参数改了前端没变 | 前端读的是 `input_schema` 序列化,确认改的是 defs 的 PortSpec |
| 通道频繁切换/全挂 | 熔断日志;`ChannelError.retryable` 是否误标 |
| import 报 `cannot import name 'dispatcher' from 'dispatch'` | 用 `importlib.import_module`(02 章 2.1 的坑) |
