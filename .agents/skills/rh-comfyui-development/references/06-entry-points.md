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
- 同名节点去重(`_deduplicate_by_name`,priority 高者胜)保留。

## 6.4 新增入口的标准姿势

1. 写协议翻译层(组装 GenerationRequest);
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
