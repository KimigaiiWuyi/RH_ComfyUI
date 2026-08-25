# 八、测试、代码红线与上线自查

## 8.1 测试

```bash
# 在插件根目录执行
python -m pytest tests/ -q     # 全离线,秒级,必须全绿
ruff check RH_ComfyUI          # 风格检查(120 列,按显示宽度计 CJK)
```

| 测试文件 | 覆盖 |
|---|---|
| `test_balancer.py` | 负载均衡选择、熔断触发与冷却恢复、scope 隔离 |
| `test_registry.py` | ModelRegistry 注册/去重/按模态查询 |
| `test_schema_validation.py` | PortSpec 通用校验 + Seedance/Wan 跨字段约束 |
| `test_dispatcher_billing.py` | 成功 settle / 校验失败不扣费 / 失败退款幂等 / BaseException 退款 / estimate_cost / settle_cost 差额对齐(禁止双重扣费) / 超时退款 |
| `test_seedance_usage_reconcile.py` | 历史单 raw usage → 实扣积分(含截断 JSON / content[] 参考视频) |
| `test_channel_failover.py` | Adapter 错误翻译、多通道故障切换、非重试错误不计熔断不切换、transient(429/503)原通道退避重试一次 |
| `test_seedance_channel.py` | SeedanceProviderChannel 凭证热更新、异常翻译、Dry-Run 透传 |
| `test_minimax_h3_model.py` / `test_minimax_h3_billing.py` | MiniMax H3 schema/分类/渲染/启用列表/按秒计费 |
| `test_gemini_image.py` | Gemini 双模判定、steps 图片提取、banana2 接线、Vertex 无 key invoke、`Gemini_Enabled_Models` 闸门 |
| `test_comfyui_enabled_workflows.py` | `ComfyUI_Enabled_Workflows` 默认空 / json 别名 / 热读增删 / AdapterChannel 闸门 |
| `test_rh_app_enabled_apps.py` | `RH_App_Enabled_Apps` 默认全开 / webappId 别名 / 热读增删 / AdapterChannel 闸门 |
| `test_openai_image.py` | 供应商池配置解析(含 weight)、sync/resync、通道凭证热更新、/images/edits 端点分流与 multipart 字段形状 |
| `test_channel_resync.py` | 外部重绑钩子登记/覆盖/失败隔离、`set_config` 监视键、重入保护 |
| `test_ensure_standard_image.py` | 参考图上传前一律 JPEG、透明铺灰底、Gemini mime 跟字节头 |
| `test_model_catalog.py` | /models 可用性以通道为准、纯 ABC 模型进清单 |
| `test_model_schema.py` | 各模型 input_schema 与能力声明一致性 |
| `test_http_contract.py` | ★ HTTP 契约快照(golden test):ModelEntry / 目录顶层 / channels 项的字段名与类型冻结,红线 4 的 CI 强制 |
| `test_concurrency_reload.py` | Max_Concurrency / 模型级并发闸热更新与非法值回落 |
| `test_provider_stats.py` | 供应商对账命令渲染(聚合行 + 熔断快照) |
| `test_video_normalize.py` | 视频预处理下沉:normalize() 统一缩放 images 与 ordered_content 图片项 |
| `test_cancel_generation.py` | cancel 登记/远程 bind、`rh_app`≠`comfyui` 通道 remote cancel、`can_resume`、模型 cancel 标志 |
| `test_statistics_request_body.py` | request_body 脱敏、**wire 优先于入参 prompt/body**、begin→update 同 id |
| `test_network_retry.py` | 传输层 5×/5s:ReadError 重试、HTTP 4xx 不重试、五次失败才抛 |

约定:用 FakeModel + FakePolicy,不 mock 网络(内核本身不应有网络调用);
统计落库用 `_mute_recording` monkeypatch 静音。
新增跨字段校验的模型必须在 `test_schema_validation.py` 补用例。
新异步 backend 应补:bind_vendor_cancel 路径 + set_wire 后 record_task 断言(或单元测 wire_capture)。

## 8.2 代码红线(违反必被打回)

1. **不绕过 `dispatch()`** 直接调 `model.run()`(计费统计会丢);
2. **`validate()` / `check_available()` 零副作用零网络**;
3. **校验先于扣费**的顺序不许动;dispatcher 失败路径"先落统计再退款"的
   顺序不许动;
4. **HTTP 契约只增不改**(`/api/RH_ComfyUI/models*` 与 `api.submit` 签名);
   由 `tests/test_http_contract.py` 的 golden test 强制 —— 该测试失败说明
   改动破坏了契约,正确做法是新增字段并把它补进快照,而不是改快照迁就实现;
5. **`core/` 不新增对上层的 import**(models/、入口包)。已存在的例外是有意的
   迁移期安排,不要模仿扩大:schema 类型物理存放在 `utils/core/`(core.schema
   re-export 为规范路径)、telemetry → `utils/database`、顶层 re-export
   `SeedanceProviderChannel`、按需读 `rh_config`;
6. **开源仓库零宿主业务硬依赖**(URL/凭证/条件 import 宿主包/
   按产品来源分叉;代码与注释不暴露具体前端或宿主后端;见 §七、§二十 §20.5);
7. 模型 `name` 是主键,改名 = 下线旧 + 上线新(统计断档),慎改;
8. `RHComfyuiTaskRecord` 既有列不改名不删;新列必须带默认值;
9. 每个新目录必须有 README.md(目录职责 + 维护须知);
10. 遵守 gsuid_core 仓库 `docs/LLM.md` 的框架级开发规范;
11. **不在 `__init__` 里把 `api_key` / `base_url` 缓存到实例属性**。
    Web 控制台改完必须**立即生效**,不重启进程。统一用 `@property` /
    `refresh_config()` / `update_credentials()` 三选一(详见
    [§11](./11-credential-hot-reload.md))。违反症状:`LocalProtocolError:
    Illegal header value b'Bearer '`。
12. **input_schema 端口必须是上游真实消费的请求参数**,不许假装。上游只吃
    aspect_ratio/size 的模型(banana 系、gpt-image-2、minimax_image01)暴露
    `ratio` 枚举;真实消费像素的(ComfyUI 工作流/rh_app:anima、qwen_2512、
    wan2.2)才保留 `width`/`height`。由 `test_model_schema.py` 与
    `test_gemini_image.py` 强制;新模型接入前先追 mapper/channel 的实际出参。

## 8.3 上线自查清单

- [ ] `pytest tests/ -q` 全绿;`ruff check` 无告警;
- [ ] 启动冒烟:`discover_builtin_models()` 日志确认模型数正确、新模型在列;
- [ ] `rh 模型列表` / `GET /api/RH_ComfyUI/models` 能看到新模型且 `available` 正确;
- [ ] 缺配置时 `unavailable_reason` 是人话;
- [ ] `knowledge_content` 写清优势/适用/不适用(Agent 选型依据);
- [ ] 真实生成一次:`RHComfyuiTaskRecord` 有记录且 point_cost / entry_point /
      channel 正确;
- [ ] 人为造一次失败(如错误参数):确认不扣费;造一次执行失败:确认退款;
- [ ] 多通道模型:关掉主通道配置,确认自动切换备用通道;
- [ ] 异步模型:进行中 cancel 一次(status=cancelled、消费记录合理);
- [ ] 异步模型:查一条 ok 记录的 `prompt`/`request_body_json` 是否为最终上游形态;
- [ ] 文档同步:模型能力变化 → `knowledge_content` + 相关 README;
      架构变化 → 本 SKILL(`.agents/skills/rh-comfyui-development/`)对应章节(含 §二十)。

## 8.4 常见故障定位

| 症状 | 先查 |
|---|---|
| 新模型三入口都看不到 | 是否加进 `ALL_MODELS`;启动日志有无注册行 |
| 模型显示不可用 | `required_config` / `requirements` 对应配置键是否已配 |
| 扣了积分没产物 | 统计表该记录 status 与退款标记(见 05 章 5.1) |
| 取消后仍扣费/双退 | 宿主是否先退再 cancel;CancelledError 路径是否再 refund(§二十) |
| 消费页 prompt 与上游日志不一致 | backend 是否 `set_wire_*`;record_task 是否 UPDATE 了 prompt(§二十 §20.4) |
| 重启后任务卡 running | 有无 vendor_task_id;宿主是否 resume_poll 或 fail+退(§二十 §20.3) |
| rh_app 目录显示可远程取消 | `_channel_supports_remote_cancel` 是否被改坏(§二十 §20.2) |
| 参数改了调用方没变 | 调用方读的是 `input_schema` 序列化,确认改的是 defs 的 PortSpec |
| 通道频繁切换/全挂 | 熔断日志;`ChannelError.retryable` 是否误标 |
| import 报 `cannot import name 'dispatcher' from 'dispatch'` | 用 `importlib.import_module`(02 章 2.1 的坑) |
| 配完 key 不重启直接报 `LocalProtocolError: Illegal header value b'Bearer '` | 老 [§11](./11-credential-hot-reload.md) 现象:单例把空 key 冻在 `__init__`,httpx 拒收 `Bearer `(尾空格)。修法见 §9.4 / §11 |
