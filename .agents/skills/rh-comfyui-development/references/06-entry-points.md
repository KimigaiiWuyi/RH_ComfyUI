# 六、三大入口

所有入口的职责只有一件事:**协议翻译** —— 把各自的输入组装成
`GenerationRequest` + `DispatchContext`,调 `dispatch()`,再把
`GenerationResult` 翻译回各自的输出形态。路由/计费/退款/统计都不在入口层。

## 6.1 命令入口(rh_generate/)

`_do_generate()` 是通用执行流程:

```python
ctx = DispatchContext(
    billing=BillingContext(user_id=ev.user_id, bot_id=ev.bot_id, entry_point=entry_point),
    policy=_POINTS_POLICY,                  # PointsBillingPolicy 单例
    on_progress=...,                        # 进度播报回调
    on_model_selected=...,                  # 扣费成功后播报"使用模型X,扣N积分"
)
result = await dispatch(request, ctx)
```

- 新命令 = 新触发器函数 + 组装 request 后走同一 `_do_generate`,
  不要复制执行逻辑;
- `entry_point` 取值:真人命令 `command`,AI 调用 `agent`
  (按 bot 类型判定,统计表据此分流)。

## 6.2 AI Agent 入口(to_ai 桥接)

生成命令通过 gsuid_core 的 `to_ai=` 触发器桥接注册为 AI 工具
(一份代码两用:真人命令 + Agent 调用)。`@ai_tools` 只用于纯管理工具
(积分查询、模型清单),因为框架规定 to_ai 与 @ai_tools 在同一函数上互斥。

Agent 智能选型的数据源(用户没指定模型时):
- `knowledge_content`(defs.py 里声明)→ AI 知识库;
- 路由第 4 级的 AI 推荐钩子(gs_agent.recommend_model,可选)。

**所以新模型的 knowledge_content 必须认真写**:优势 / 适用场景 /
不适用场景 / 成本,这是 Agent 替用户选模型的唯一依据。

## 6.3 HTTP 入口(api.py + rh_models/)

- `api.submit()`:外部插件调用的编程接口,内部构造
  `ExternalPrepaidPolicy`(调用方已扣费,引擎只记账)+
  `entry_point="http"` 走 dispatch(统计表 entry_point:
  command / agent / http);
  常用 re-export:`submit` / `get_point_cost` / `list_models` /
  `cancel_generation` / `resume_poll` / `charge_points` / …;
- `cancel_generation(trace_id=…|record_id=…)`:取消本进程进行中任务
  (先上游 cancel 再 Task.cancel);详见 [§二十](./20-cancel-resume-and-wire-audit.md);
- `resume_poll(model=…, vendor_task_id=…, …)`:**不走 dispatch**、不二次扣费,
  仅按上游 id 续轮询;供宿主启动恢复;
- `rh_models/api.py`:`GET /api/RH_ComfyUI/models*` 聚合
  (`supports_cancel` / `supports_remote_cancel` / `channels[]`);
- `POST /api/RH_ComfyUI/tasks/cancel`:HTTP 取消
  (body: `trace_id` 或 `record_id`)。

### HTTP 契约红线(调用方依赖)

- `ModelEntry` 既有字段(name/display_name/task_type/backend/point_cost/
  input_schema/output_schema/…)**不得改名、改类型、删除**;
- 新增字段必须带默认值。已冻结增量示例:
  - 2026-07:`card` / `channels` / `execution_mode` /
    `accepts_images` / `max_input_images` / `point_range` / `catalog_group`
  - 2026-08:`supports_cancel` / `supports_remote_cancel`
    (及 `channels[].supports_*`);
- `input_schema` 直接序列化模型的 PortSpec —— 改模型端口 = 改调用方表单;
- 同名节点去重(`_deduplicate_by_name`,priority 高者胜)保留;
- catalog-only 键(`frame_mode` / `image_size` / `task_mode` …)经 submit 未知
  kwargs 进入 `params`,见 [§6.6](#66-动态参数进-params新模型标准)。

## 6.4 新增入口的标准姿势

1. 写协议翻译层(组装 GenerationRequest;catalog-only 键进 `params`,见 [§6.6](#66-动态参数进-params新模型标准));
2. 选/写 BillingPolicy(独立钱包才需要新写);
3. `DispatchContext(billing=BillingContext(..., entry_point="新入口名"), policy=...)`;
4. 调 `dispatch()`;禁止直接调 `model.run()`。

## 6.5 宿主侧取消 / 恢复(推荐编排)

引擎不绑定具体业务任务表。宿主若自管 job 与预扣积分,推荐:

```
取消:  mark job cancelled → 退预扣一次 → cancel_generation(trace_id=job_id)
       → 生成协程若已 cancelled 则跳过 failed/二次退款

启动恢复:
  for zombie job:
    RH running 行 by trace_id → extra_params.vendor_task_id?
      yes → resume_poll(...) → 落盘写回(不 charge)
      no  → mark failed + 退款
```

详见 [§二十](./20-cancel-resume-and-wire-audit.md) §20.2–20.3。

## 6.6 动态参数进 params(新模型标准)

`input_schema` 的 PortSpec 名是调用方表单与引擎之间的**唯一键名**。
这些键**不一定**是 `GenerationRequest` 的 dataclass 字段。
接新大模型时沿用这一套,不要另起一套 HTTP 形状或给每个 enum 加顶层字段。

### 分流

| 键 | 落点 | 谁读 |
|----|------|------|
| `prompt` / `images` / `video_refs` / `audio_refs` / `ordered_content` / `ratio` / `width` / `height` / `resolution` / `duration` / `seed` / `generate_audio` / `watermark` / `model` / `omni_reference_task_type` / 语音字段 / `user_id` / `trace_id` / `params` 本身 | `GenerationRequest` 顶层 | 基类 validate / 路由 / 部分 mapper |
| **不在 dataclass 上的 catalog 键**(当前:`frame_mode` / `image_size` / `quality` / `size_mode` / `task_mode` / `output_format` / `input_video_duration`,以及以后每个新 enum) | **`request.params`** | 该模型的 `validate` / classify / mapper / `estimate_cost` |

**禁止**每接一个模型就给 `GenerationRequest` 加同名字段。只有跨模态、多数模型共用、语义稳定的键才升顶层(现成例子:`ratio` / `duration` / `resolution`)。升顶层必须同时改 dataclass **和** `api._build_request` 的 `handled` 集合 —— 只改一边会 `TypeError`,或键被吞进 params。

### `api.submit` 怎么把顶层 kwargs 送进 params

`submit(..., **kwargs)` → `_build_request`(`RH_ComfyUI/api.py`):

1. `handled` 集合里的键 → `GenerationRequest` 顶层
2. **其余键** → `passthrough`,再 `existing_params.update(passthrough)` 写入 `request.params`
3. 同名键同时出现在 `params=` 与顶层 kwargs 时,**顶层 kwargs 覆盖 params**
4. `frame_mode` 不是 dataclass 字段,必须走 2。空/`auto` 且扁平 `images` 全部 `role=reference` 时,引擎把 `params.frame_mode` 写成 `reference`(否则 Seedance 系会把多参考图当首尾帧并强制 `ratio=adaptive`)

命令 / Agent 入口(`rh_generate`)是直接 `GenerationRequest(...)`,**没有**这层 passthrough。动态键必须手写 `params={...}`。

宿主 HTTP 层可以在调 `submit` 前把 JSON 顶层未知键抄进 `params`;引擎不依赖任何具体宿主。`submit` 自身已做 passthrough,抄或不抄都能到 `params`。

`GET /models/estimate` 是另一条组装路径:Query 显式写入 `request.params`(及部分顶层字段)。新计费维度必须:estimate 签名 + 写入 params 的键 + `estimate_cost` 读键 **三同名**。详见 [§16.3](./16-models-catalog-api.md)。

### 读侧铁律(加模型必守)

- `validate` / classify / mapper / `estimate_cost` **只从** `request.params.get("键")` 读 catalog-only 键,不要假设 dataclass 上有同名字段
- PortSpec 名 = params 键 = estimate_cost 读键 = estimate API 写入 params 的键
- 历史别名用 `or` 兼容,不要再发明第三套名字。现成:`image_size` 优先,`size_mode` fallback([§15.4 bug #6](./15-billing-pricing-formulas.md))
- 模型不认识的 params 键**静默忽略**;不要为「这个模型用不到」拒绝整单

```python
# 错:给单一模型开关加 dataclass 字段
class GenerationRequest:
    frame_mode: Optional[str] = None

# 错:validate 读顶层,但 submit 把未知键放进了 params
fm = request.frame_mode  # 永远 None

# 对:schema 有 frame_mode 端口,validate / classify 读 params
fm = str((request.params or {}).get("frame_mode") or "auto")
```
