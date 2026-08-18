# 二十、任务取消 · resume-poll · 最终 wire 落库(2026-08)

> 本章汇总 **取消能力声明**、**进程重启后按上游 task_id 续轮询**、**统计表
> prompt/请求体以实际上网载荷为准**、以及 **开源边界** 交接要点。
> 改取消 / 启动恢复 / 消费页展示 prompt 前必读。

## 20.1 模块与入口一览

| 能力 | 路径 | 公开入口 |
|------|------|----------|
| 进行中任务表 | `core/dispatch/active_tasks.py` | `cancel_generation(trace_id=…\|record_id=…)` |
| 上游 task 绑定 | 同上 `bind_vendor_cancel` | provider 创建任务成功后调用 |
| resume-poll | `core/dispatch/resume.py` | `from RH_ComfyUI import resume_poll` / `api.resume_poll` |
| 最终 wire 捕获 | `core/telemetry/wire_capture.py` | backend `set_wire_from_http_body` / `set_wire_audit` |
| 统计写入 | `utils/database/statistics.py` | `begin_task` / `record_task`(终态优先 wire) |
| 目录取消字段 | `rh_models/api.py` | `supports_cancel` / `supports_remote_cancel` + `channels[]` |
| HTTP 取消 | `rh_models/webapi.py` | `POST /api/RH_ComfyUI/tasks/cancel` |

外层 re-export:`RH_ComfyUI/__init__.py` 含 `cancel_generation`、`resume_poll`。

---

## 20.2 主动取消

### 流程

```
cancel_generation(trace_id | record_id)
  → 查 ActiveTaskRegistry
  → 若有 cancel_remote 且未尝试过: await cancel_remote()  # 上游 DELETE/cancel
  → task.cancel()                                        # 本进程 asyncio.Task
  → dispatch except CancelledError
       → record_dispatch(status=cancelled) + policy.refund
```

- **仅本进程有效**;多 worker 只能取消本 worker 登记的任务。
- 上游取消失败仍继续本地 cancel(日志 warning)。

### 能力声明(只增不改)

| 字段 | 层级 | 含义 |
|------|------|------|
| `supports_cancel` | 模型 / 通道 | 本进程进行中任务可被打断;未知模型 **False** |
| `supports_remote_cancel` | **供应商/通道** | 创建上游任务后可远程 cancel/DELETE;未知供应商 **False** |

**`supports_remote_cancel` 是供应商级**,不是模型 ClassVar:
- 真值源:`ProviderChannel.supports_remote_cancel()`(ark/comfyui/gemini/dashscope/
  网关异步等覆盖;默认 False)
- 目录:`_channel_supports_remote_cancel(..., channel=)` → `channels[]`;顶层 = OR
- 模型 ClassVar 仅软提示,不参与目录 remote 判定
- **禁止**因共用 `RH_apikey` 把 AI 应用(`rh_app`)误标为可 cancel

### `rh_app` ≠ `comfyui`(必背)

即便共用 RunningHub `RH_apikey`,backend / API 完全不同:

| backend / channel | 上游形态 | remote cancel | resume-poll |
|-------------------|----------|---------------|-------------|
| **`comfyui`** | 本地 Comfy 或 RH **工作流** `POST /prompt` | ✅ 本地 queue+interrupt 或 RH `POST /task/openapi/cancel` | ✅ `/history` |
| **`rh_app`** | RH **AI 应用** `/openapi/v2/run/ai-app` | ❌ **禁止**本地/远程 cancel(只能 resume 继续) | ✅ `/openapi/v2/query` |
| Seedance `ark` | 方舟 generations tasks | ✅ `DELETE .../tasks/{id}` | ✅ poll |
| Seedance 通道名 `runninghub` | Seedance RH 视频端点(**不是** Comfy 工作流 cancel 那条) | 跟模型 flag | ✅ poll |
| HappyHorse `dashscope` | DashScope async | ✅ `POST .../tasks/{id}/cancel`(**仅 PENDING**) | ✅ poll |
| MiniMax H3 `minimax-h3` | `DELETE /v2/video_generation/{id}` | ✅ **仅 queued** 可取消;running 上游拒绝,本地仍停轮询 | ✅ `GET /v2/query/video_generation/{id}`(7 天) |
| Gemini 生图 | `generate_content` 单次请求 | ❌ 无上游 task(本地仍可打断) | 仅历史 interaction id |

桥接模型(`models/bridge.py`):仅 `node.backend in ("comfyui", "gemini-image")`
时默认 `supports_remote_cancel=True`;**`rh_app` 保持 False**。

### 目录 `/models` 透出(前后端统一契约)

前端**只根据** `GET /api/RH_ComfyUI/models` 决定能否取消,禁止写死模型名:

| 读哪个字段 | 何时 |
|------------|------|
| 模型顶层 `supports_cancel` | 单通道 / 粗粒度 UI |
| `channels[当前通道].supports_cancel` | 多通道(如 Seedance 选 ark vs runninghub) |

- 顶层 = 各通道 `supports_*` 的 **OR**(实现 `_aggregate_cancel_flags`)
- `rh_app`:顶层与通道全 **false**(只能 resume)
- 未知模型/未知供应商通道:**false**(fail-closed;宿主 cancel 门禁同此)
- `POST /tasks/cancel` 与上述标志一致:false 时 `ok=false`

```json
{
  "execution_mode": "async_poll",
  "supports_cancel": true,
  "supports_remote_cancel": true,
  "channels": [
    {
      "name": "ark",
      "vendor_model": "...",
      "available": true,
      "supports_cancel": true,
      "supports_remote_cancel": true
    }
  ]
}
```

`tests/test_http_contract.py` + `test_model_catalog` 冻结/回归上述字段;加字段补 golden,禁止删改。

### 宿主侧取消(非本插件职责,交接备忘)

业务宿主若在引擎外包积分预扣,取消时应:

1. 先标自身任务终态(防迟到结果落盘)
2. **退积分一次**
3. 再调 `cancel_generation(trace_id=…)`
4. 生成协程 `Exception` / `BaseException` 路径若发现已 cancelled,**禁止**再 mark failed / 再退一次

引擎自身:dispatch CancelledError 路径会退 `PointsBillingPolicy` 预扣;
`ExternalPrepaidPolicy` 入口由宿主负责钱包,引擎只记 status。

---

## 20.3 resume-poll(进程重启后续跑)

### 动机

进程 kill 后 `ActiveTaskRegistry` 与本机 `asyncio.Task` 清空,但上游
异步任务可能仍在跑。有 **持久化的 `vendor_task_id`** 时可只 poll 不重 create。

### 持久化

`bind_vendor_cancel(vendor_task_id=…, channel_name=…)` 除挂内存 cancel 外,
异步把字段合并进当前 `RHComfyuiTaskRecord.extra_params_json`:

```json
{
  "vendor_task_id": "cgt-xxx",
  "vendor_channel": "ark"
}
```

关联键:宿主若在 `submit(..., trace_id=job_id)` 对齐,可用 `trace_id` 反查 running 行。

### API

```python
from RH_ComfyUI import resume_poll
# 或: from RH_ComfyUI.core.dispatch.resume import resume_poll, can_resume

result = await resume_poll(
    model="seedance2",
    vendor_task_id="...",
    channel="ark",          # 可选;丢失时 seedance 优先 ark
    backend="seedance",     # 可选;可从 model/channel 推断
    kind="video",           # 可选
    trace_id="same-as-submit",  # 恢复期间仍可 cancel_generation
    record_id=123,          # 可选;成功/失败更新该统计行
    on_progress=...,
)
# → api.GenerationResult(kind/data/mime/metadata…);不走 dispatch,不二次扣费
```

`can_resume(backend=…, model=…, channel=…, vendor_task_id=…)`:粗判能否 resume。

### 支持矩阵(与 §20.2 表一致)

- seedance / happyhorse / minimax-h3 / rh_app / comfyui / gemini-image(及同类)
- **不支持**:纯 sync、无 task_id、未知 backend → 宿主应 fail+退款,勿半吊子 poll

### 异常

| 类型 | 含义 | 宿主建议 |
|------|------|----------|
| `ResumeNotSupportedError` | 无法 resume | fail + 退积分 |
| `ResumeFailedError` | 上游失败/无产物 | fail + 退积分 |
| `ResumeCancelledError` | 上游已取消(非本机 user cancel) | 记 cancelled + 退积分(非 http) |
| `asyncio.CancelledError` | 用户取消(非 rh_app) | 见下「resume 退款」 |

### resume 退款(防双退)

`_finalize_record` 规则:

1. **仅 `status=running`** 的统计行可写终态;已 `ok/failed/cancelled` **不覆盖、不退款**
2. 失败/取消退 RHBind 用条件 `UPDATE … WHERE status=running AND refunded=0` 抢占,**防并发双退**
3. **`entry_point=http`(ExternalPrepaid):引擎不退 RHBind**,宿主钱包自管(与 §20.2 一致)
4. `command` / `agent` 等:原 dispatch 走 `PointsBillingPolicy` 预扣 → 引擎退 RHBind 并 `refunded=True`

宿主约定:**传了 `record_id` 且入口非 http 时,失败/取消由引擎退 RHBind,宿主勿再退同一笔。**

### 限制(诚实边界)

- 仅 **create 成功且已 bind** 的任务可 resume;死在 create 前 → 无 task_id
- 本地 Comfy history 若已丢 → resume 失败
- resume **不重放** 原始 GenerationRequest 媒体;只 GET/poll 已有任务

---

## 20.4 统计落库:最终 wire(prompt + request body)

### 问题

`begin_task` 只能记**调用方入参**。Seedance 等会在 POST 前:

- 改写引用语法(`transform_prompt`:`[参考图片N]` / `图片N` → 供应商语法)
- 注入 `【图片N】` 进 content[] 文本段
- 组装完整 HTTP body(含 materialize 后的 URL)

若终态仍写入口 `request.prompt` / 入口 body,**消费页展示与实际上游不一致**。

### 机制

```
dispatch 开始 → clear_wire_audit()
backend render_create / 提交前 → set_wire_from_http_body(masked_body)
  或 set_wire_audit(prompt=…, request=…)
record_task 终态 → 优先 wire → 覆盖 prompt + request_body_json
dispatch finally → clear_wire_audit()
```

| 列 | 优先级 |
|----|--------|
| `prompt` | `wire_capture.prompt` → `result.metadata.wire_prompt` → `request.prompt` |
| `request_body_json` | `wire_capture.request` → `metadata.wire_request` → 入参 `request_body` |

- wire body 应经 `mask_body`(seedance/happyhorse 已 mask 再 set)
- `update_task_record` 支持写 `prompt=` / `request_body_json=`
- 失败路径:若 POST 前已 set_wire,失败记录也会带上最终 body(便于排障)

### 已接线 backend

| backend | 捕获点 |
|---------|--------|
| Seedance | `provider.run` 在 `_request` 前 `set_wire_from_http_body(masked)` |
| HappyHorse | 同上 |
| MiniMax H3 | `h3_provider.run` 在 `_request` 前 `set_wire_from_http_body(masked)` |
| rh_app | submit 前 `webappId` + `nodeInfoList` + 入参 prompt |
| ComfyUI | `/prompt` 成功后 `request=p`(workflow 图) |

新 backend **必须**在真正发出 HTTP 前 `set_wire_*`,否则统计回落入参。

### 测试

`tests/test_statistics_request_body.py::test_record_task_prefers_wire_audit_prompt_and_body`

---

## 20.5 开源边界(插件独立性)

RH_ComfyUI 是**开源独立插件**:

1. **不得** `import` / soft-import 宿主业务包(业务画布后端、聚合网关、账号系统等)
2. **不得**在源码注释/文档示例中绑定具体宿主产品路径作为硬依赖
3. 宿主能力一律扩展点注入:
   - `set_media_publisher` — bytes → 公网 URL
   - `channel_registry.register_binding` — 外挂通道
   - `register_resync_hook` / `bind_config_resync` — 配置改完重挂绑定
   - `BillingPolicy` 子类 — 独立钱包
   - `model_registry` / entry points — 闭源模型
4. 公开 API 用中性词:**调用方 / 宿主 / 外部插件**,不用具体产品名当架构前提
5. `bot_id` 是自由字符串(如 `"qq"` / `"canvas"` 均可);`CANVAS_BOT_ID` 仅文档化常量,非硬绑定

详见 [§七 闭源接入](./07-closed-source-extension.md) §7.4–7.5。

---

## 20.6 交接检查清单

改取消 / resume / 统计展示时:

- [ ] 新异步 backend:create 成功后 `bind_vendor_cancel` + POST 前 `set_wire_*`
- [ ] 模型 `supports_remote_cancel` 与通道 `_channel_supports_remote_cancel` 一致
- [ ] `rh_app` 永不声明 remote cancel;Comfy 工作流可以
- [ ] `/models` golden 字段含 cancel 能力
- [ ] resume 支持矩阵更新 + `can_resume` 覆盖
- [ ] 宿主取消路径:单次退款、cancelled 去重
- [ ] `pytest tests/test_cancel_generation.py tests/test_statistics_request_body.py tests/test_http_contract.py` 全绿
- [ ] 开源包无宿主产品硬依赖 import/注释

## 20.7 相关测试文件

| 文件 | 覆盖 |
|------|------|
| `tests/test_cancel_generation.py` | cancel 登记、远程 bind、rh_app/comfyui 通道区分、`can_resume` |
| `tests/test_statistics_request_body.py` | 脱敏 body、wire 优先于入参 prompt/body、begin→update |
| `tests/test_http_contract.py` | ModelEntry / channels 含 `supports_cancel` 等字段冻结 |
| `tests/test_dispatcher_billing.py` | CancelledError → status=cancelled + 退款 |

---

## 20.8 代码索引(快速跳转)

```
RH_ComfyUI/
  core/dispatch/active_tasks.py   # 登记 / cancel / bind / persist vendor_task_id
  core/dispatch/resume.py         # resume_poll / can_resume / 异常类型
  core/dispatch/dispatcher.py     # clear_wire_audit 生命周期
  core/telemetry/wire_capture.py  # set/get/clear wire
  core/telemetry/recorder.py      # begin/record_dispatch
  utils/database/statistics.py    # record_task 优先 wire
  utils/database/models.py        # RHComfyuiTaskRecord.update_task_record(prompt=…)
  rh_models/api.py                # _channel_supports_remote_cancel + ModelEntry
  rh_models/webapi.py             # /models + /tasks/cancel
  models/bridge.py                # comfyui/gemini-image remote cancel 默认
  utils/backends/seedance/provider.py   # wire + bind cancel
  utils/backends/happyhorse/provider.py
  utils/backends/minimax/h3_provider.py # H3 create/poll/DELETE + wire + bind cancel
  utils/backends/rh_app/executor.py     # 无 remote cancel 注释
  utils/backends/comfyui/api.py         # cancel_task + wire workflow
  api.py                          # cancel_generation / resume_poll 公开 API
```
